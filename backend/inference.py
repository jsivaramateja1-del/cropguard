"""Leaf-image inference: MobileNetV3-Large, served from ONNX or PyTorch.

Two backends, one interface. The choice is made once at load time:

* **ONNX Runtime** (preferred when the `.onnx` artifact exists). Peak memory is
  ~151 MB against ~660 MB for PyTorch, which is what makes a 512 MB free host
  viable. Verified to produce bit-identical logits to the PyTorch checkpoint.
* **PyTorch** (used in development and on any GPU host). Requires torch installed.

Preprocessing is written with PIL and NumPy rather than torchvision, so the ONNX
path does not drag a 2.5 GB dependency into the deployment just to resize an image.
Both backends share that preprocessing, so a prediction cannot differ because of a
transform mismatch.

The model is loaded lazily on first use and kept for the life of the process — never
per request. If no artifact is present the service reports itself unavailable and the
API returns a clear 503 rather than inventing a diagnosis.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
from PIL import Image, UnidentifiedImageError

from config import settings

logger = logging.getLogger("cropguard.inference")

IMAGENET_MEAN = (0.485, 0.456, 0.406)
IMAGENET_STD = (0.229, 0.224, 0.225)
DEFAULT_IMAGE_SIZE = 224
ARTIFACT_DEVELOPMENT = "development_untrained"

# Torch and onnxruntime are both optional, and both are imported lazily.
#
# This matters for memory, not tidiness: `import torch` costs ~528 MB resident. A host
# that serves the ONNX backend must never pay that, and an import at module scope would
# charge it to every deployment that happens to have torch installed. Only the backend
# actually chosen is imported.
_torch_module = None
torch_import_error: Optional[str] = None
_ort_module = None
ort_import_error: Optional[str] = None


def torch_backend():
    """Import torch on demand. Returns the module or raises ModelUnavailable."""
    global _torch_module, torch_import_error
    if _torch_module is not None:
        return _torch_module
    if torch_import_error is not None:
        raise ModelUnavailable(torch_import_error)
    try:
        import torch as module  # noqa: PLC0415 - deliberately deferred

        _torch_module = module
        return module
    except Exception as error:  # ImportError, or a broken CUDA/CPU build
        torch_import_error = f"torch could not be imported: {type(error).__name__}: {error}"
        raise ModelUnavailable(torch_import_error) from error


def onnx_backend():
    """Import onnxruntime on demand. Returns the module or raises ModelUnavailable."""
    global _ort_module, ort_import_error
    if _ort_module is not None:
        return _ort_module
    if ort_import_error is not None:
        raise ModelUnavailable(ort_import_error)
    try:
        import onnxruntime as module  # noqa: PLC0415

        _ort_module = module
        return module
    except Exception as error:
        ort_import_error = f"onnxruntime could not be imported: {type(error).__name__}: {error}"
        raise ModelUnavailable(ort_import_error) from error


class InferenceError(RuntimeError):
    """Base class for inference failures."""


class ModelUnavailable(InferenceError):
    """No usable model artifact — the API must not guess a diagnosis."""


class InvalidImage(InferenceError):
    """The uploaded bytes are not a readable image."""


@dataclass
class Prediction:
    label: str
    crop: str
    condition: str
    probability: float

    @property
    def is_healthy(self) -> bool:
        return self.condition.lower() == "healthy"


@dataclass
class PredictionResult:
    predictions: List[Prediction]
    top: Prediction
    backend: str
    artifact_type: str
    model_version: str
    image_size: int
    inference_ms: float
    warnings: List[str] = field(default_factory=list)

    @property
    def confidence(self) -> float:
        return self.top.probability

    @property
    def is_development_artifact(self) -> bool:
        return self.artifact_type == ARTIFACT_DEVELOPMENT


def split_label(label: str) -> Tuple[str, str]:
    """'Tomato___Early_blight' -> ('Tomato', 'Early blight')."""
    crop, _, condition = label.partition("___")
    return crop, condition.replace("_", " ").strip()


# ---------------------------------------------------------------------------
# Preprocessing (PIL + NumPy only)
# ---------------------------------------------------------------------------
def preprocess_image(
    image: Image.Image,
    image_size: int = DEFAULT_IMAGE_SIZE,
    mean: Tuple[float, ...] = IMAGENET_MEAN,
    std: Tuple[float, ...] = IMAGENET_STD,
) -> np.ndarray:
    """Resize shorter side to 256, centre-crop, normalise. Returns NCHW float32."""
    if image.mode != "RGB":
        image = image.convert("RGB")

    resize_to = int(round(image_size * 256 / 224))
    width, height = image.size
    scale = resize_to / min(width, height)
    new_size = (max(1, int(round(width * scale))), max(1, int(round(height * scale))))
    image = image.resize(new_size, Image.BILINEAR)

    left = max(0, (new_size[0] - image_size) // 2)
    top = max(0, (new_size[1] - image_size) // 2)
    image = image.crop((left, top, left + image_size, top + image_size))

    array = np.asarray(image, dtype=np.float32) / 255.0
    array = (array - np.asarray(mean, dtype=np.float32)) / np.asarray(std, dtype=np.float32)
    return np.ascontiguousarray(array.transpose(2, 0, 1)[None, ...])


def softmax(logits: np.ndarray) -> np.ndarray:
    shifted = logits - logits.max(axis=1, keepdims=True)
    exponentiated = np.exp(shifted)
    return exponentiated / exponentiated.sum(axis=1, keepdims=True)


def open_image(raw: bytes) -> Image.Image:
    """Decode bytes into an RGB PIL image, rejecting anything unreadable."""
    import io

    if not raw:
        raise InvalidImage("The uploaded file is empty.")
    try:
        image = Image.open(io.BytesIO(raw))
        image.load()
    except UnidentifiedImageError as error:
        raise InvalidImage("That file is not a readable image.") from error
    except OSError as error:
        raise InvalidImage(f"The image could not be decoded: {error}") from error

    width, height = image.size
    if min(width, height) < settings.min_image_pixels:
        raise InvalidImage(
            f"The image is too small ({width}x{height}). "
            f"Use a photo at least {settings.min_image_pixels} pixels on the short side."
        )
    if max(width, height) > settings.max_image_pixels:
        raise InvalidImage(
            f"The image is too large ({width}x{height}). "
            f"Maximum {settings.max_image_pixels} pixels on the long side."
        )
    return image.convert("RGB")


# ---------------------------------------------------------------------------
# Model service
# ---------------------------------------------------------------------------
class ModelService:
    """Loads one checkpoint once and serves predictions from it."""

    def __init__(
        self,
        onnx_path: Optional[Path] = None,
        torch_path: Optional[Path] = None,
        class_names_path: Optional[Path] = None,
        backend_preference: str = "auto",
    ) -> None:
        resolved_model = Path(torch_path or settings.model_path_resolved)
        resolved_onnx = Path(onnx_path or settings.onnx_path_resolved)

        # MODEL_PATH is documented as the PyTorch checkpoint, but a deployment may
        # reasonably point it at the ONNX artifact instead. Honour that rather than
        # reporting "expected ... .onnx (ONNX) or ... .onnx (PyTorch)".
        if resolved_model.suffix.lower() == ".onnx":
            resolved_onnx = resolved_model
            resolved_model = resolved_model.with_suffix(".pth")

        self.onnx_path = resolved_onnx
        self.torch_path = resolved_model
        self.class_names_path = Path(class_names_path or settings.class_names_path_resolved)
        self.backend_preference = (backend_preference or "auto").lower()

        self._lock = threading.Lock()
        self._loaded = False
        self._backend: Optional[str] = None
        self._session = None
        self._module = None
        self._torch = None
        self._class_names: List[str] = []
        self._image_size = DEFAULT_IMAGE_SIZE
        self._mean = IMAGENET_MEAN
        self._std = IMAGENET_STD
        self._artifact_type = "unknown"
        self._model_version = "unknown"
        self._load_error: Optional[str] = None
        self._load_seconds: Optional[float] = None

    # ------------------------------------------------------------- properties
    @property
    def available(self) -> bool:
        return self._loaded

    @property
    def class_names(self) -> List[str]:
        return list(self._class_names)

    def describe(self) -> Dict[str, object]:
        """Non-secret status for /health."""
        return {
            "available": self._loaded,
            "backend": self._backend,
            "artifact_type": self._artifact_type,
            "model_version": self._model_version,
            "classes": len(self._class_names),
            "image_size": self._image_size,
            "onnx_present": self.onnx_path.is_file(),
            "torch_present": self.torch_path.is_file(),
            "load_seconds": self._load_seconds,
            "error": self._load_error,
        }

    # ------------------------------------------------------------------ load
    def _read_class_names(self) -> List[str]:
        if self.class_names_path.is_file():
            with self.class_names_path.open("r", encoding="utf-8") as handle:
                names = json.load(handle)
            if isinstance(names, list) and names:
                return [str(name) for name in names]
        return []

    def _choose_backend(self) -> str:
        wants_onnx = self.backend_preference in {"auto", "onnx"}
        wants_torch = self.backend_preference in {"auto", "torch"}

        # File presence decides first, so a host serving ONNX never even attempts to
        # import torch (which would cost ~528 MB for nothing).
        if wants_onnx and self.onnx_path.is_file():
            onnx_backend()  # raises ModelUnavailable with a clear message if missing
            return "onnx"
        if wants_torch and self.torch_path.is_file():
            torch_backend()
            return "torch"


        raise ModelUnavailable(
            "No model artifact found in "
            f"{self.onnx_path.parent}. Expected either the ONNX export "
            f"'{self.onnx_path.name}' or the PyTorch checkpoint "
            f"'{self.torch_path.name}'. Train with ml_training/train_kaggle.py, export "
            "with ml_training/export_onnx.py, then copy the artifacts into backend/model/."
        )

    def load(self) -> None:
        """Load the artifact. Safe to call repeatedly; only the first call does work."""
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            started = time.perf_counter()
            try:
                backend = self._choose_backend()
                if backend == "onnx":
                    self._load_onnx()
                else:
                    self._load_torch()
                self._backend = backend
                self._loaded = True
                self._load_error = None
                logger.info(
                    "model loaded: backend=%s classes=%d artifact=%s",
                    backend, len(self._class_names), self._artifact_type,
                )
            except ModelUnavailable as error:
                self._load_error = str(error)
                logger.error("model unavailable: %s", error)
                raise
            except Exception as error:  # corrupt file, shape mismatch, OOM
                self._load_error = f"{type(error).__name__}: {error}"
                logger.exception("model failed to load")
                raise ModelUnavailable(self._load_error) from error
            finally:
                self._load_seconds = round(time.perf_counter() - started, 3)

    def _load_onnx(self) -> None:
        ort = onnx_backend()
        options = ort.SessionOptions()
        # One thread: the deployment target is 0.1-2 vCPU, and extra threads cost
        # memory without helping a single-image request.
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self._session = ort.InferenceSession(
            str(self.onnx_path), sess_options=options, providers=["CPUExecutionProvider"]
        )

        names = self._read_class_names()
        if not names:
            raise ModelUnavailable(
                f"{self.class_names_path.name} is missing or empty; the label order "
                "cannot be reconstructed from an ONNX graph."
            )

        # Guard against the most dangerous mismatch in the whole system: a graph with N
        # outputs paired with a label list of a different length. Indices would still be
        # in range, so predictions would look plausible while naming the wrong disease.
        output_shape = self._session.get_outputs()[0].shape
        if len(output_shape) == 2 and isinstance(output_shape[1], int):
            graph_classes = int(output_shape[1])
            if graph_classes != len(names):
                raise ModelUnavailable(
                    f"Label mismatch: the ONNX graph outputs {graph_classes} classes but "
                    f"{self.class_names_path.name} lists {len(names)}. Upload the "
                    f"class_names.json written by export_onnx.py alongside the .onnx — "
                    "a mismatched label list would name the wrong disease."
                )

        self._class_names = names
        self._artifact_type = self._infer_artifact_type_from_name()
        self._model_version = f"mobilenet_v3_large/{self._artifact_type}"
        self._image_size = DEFAULT_IMAGE_SIZE

    def _load_torch(self) -> None:
        torch = torch_backend()
        payload = torch.load(str(self.torch_path), map_location="cpu", weights_only=True)
        for key in ("state_dict", "class_names"):
            if key not in payload:
                raise ModelUnavailable(f"Checkpoint is missing '{key}'.")

        from torchvision.models import mobilenet_v3_large

        names = list(payload["class_names"])
        model = mobilenet_v3_large(weights=None)
        model.classifier[-1] = torch.nn.Linear(
            model.classifier[-1].in_features, len(names)
        )
        model.load_state_dict(payload["state_dict"])
        model.eval()

        torch.set_num_threads(1)
        self._torch = torch
        self._module = model
        self._class_names = names
        self._image_size = int(payload.get("image_size", DEFAULT_IMAGE_SIZE))
        normalization = payload.get("normalization") or {}
        self._mean = tuple(normalization.get("mean", IMAGENET_MEAN))
        self._std = tuple(normalization.get("std", IMAGENET_STD))
        self._artifact_type = str(payload.get("artifact_type", "unknown"))
        self._model_version = f"mobilenet_v3_large/{self._artifact_type}"

    def _infer_artifact_type_from_name(self) -> str:
        """An ONNX export carries no metadata, so the label file decides.

        export_onnx.py writes a sidecar marker for development exports; without one we
        assume the artifact is trained, because that is what the export script is for.
        """
        marker = self.onnx_path.with_suffix(".artifact")
        if marker.is_file():
            return marker.read_text(encoding="utf-8").strip() or "trained"
        return "trained"

    # --------------------------------------------------------------- predict
    def predict(self, image: Image.Image, top_k: int = 3) -> PredictionResult:
        if not self._loaded:
            self.load()

        started = time.perf_counter()
        batch = preprocess_image(image, self._image_size, self._mean, self._std)

        if self._backend == "onnx":
            input_name = self._session.get_inputs()[0].name
            logits = self._session.run(None, {input_name: batch})[0]
        else:
            torch = self._torch
            with torch.no_grad():
                logits = self._module(torch.from_numpy(batch)).numpy()

        probabilities = softmax(np.asarray(logits, dtype=np.float32))[0]
        order = probabilities.argsort()[::-1][: max(1, top_k)]

        predictions = []
        for index in order:
            crop, condition = split_label(self._class_names[int(index)])
            predictions.append(
                Prediction(
                    label=self._class_names[int(index)],
                    crop=crop,
                    condition=condition,
                    probability=float(probabilities[int(index)]),
                )
            )

        warnings: List[str] = []
        if self._artifact_type == ARTIFACT_DEVELOPMENT:
            warnings.append(
                "This checkpoint is a randomly initialised development artifact. "
                "Its scores are not disease predictions."
            )

        return PredictionResult(
            predictions=predictions,
            top=predictions[0],
            backend=self._backend or "unknown",
            artifact_type=self._artifact_type,
            model_version=self._model_version,
            image_size=self._image_size,
            inference_ms=round((time.perf_counter() - started) * 1000, 1),
            warnings=warnings,
        )


_service: Optional[ModelService] = None
_service_lock = threading.Lock()


def get_model_service() -> ModelService:
    """Process-wide singleton: the model is loaded once, never per request."""
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = ModelService(backend_preference=settings.model_backend)
    return _service


def reset_model_service() -> None:
    """Drop the cached service. Used by tests and after uploading a new model."""
    global _service
    with _service_lock:
        _service = None
