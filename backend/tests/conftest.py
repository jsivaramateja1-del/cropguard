"""Test bootstrap.

Prepares the environment *before* any application module is imported:

* DATABASE_URL points at a throwaway SQLite file, so tests never touch the developer's
  cropguard_local.db.
* A tiny synthetic ONNX model is generated and pointed at by ONNX_PATH, so the ONNX
  inference path and the whole API can be tested without torch, without a trained
  checkpoint and without a network call. The graph is a global-average-pool plus a
  Gemm, which makes the winning class deterministic for any input — enough to exercise
  the plumbing, not to pretend we are testing accuracy.
* backend/ is placed on the import path so modules import exactly as uvicorn imports
  them.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

_TMP = Path(tempfile.mkdtemp(prefix="cropguard-tests-"))
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP / 'test.db'}"
os.environ.setdefault("FRONTEND_ORIGIN", "http://localhost:5173")
os.environ.setdefault("CONFIDENCE_THRESHOLD", "0.75")

TEST_CLASSES = [
    "Tomato___Early_blight",
    "Tomato___Late_blight",
    "Tomato___healthy",
    "Rice___Brown_spot",
]


def _build_tiny_onnx(path: Path, num_classes: int) -> None:
    """GlobalAveragePool -> Flatten -> Gemm. Deterministic, torch-free."""
    import numpy as np
    import onnx
    from onnx import TensorProto, helper, numpy_helper

    weights = np.zeros((3, num_classes), dtype=np.float32)
    weights[0, 0] = 1.0  # a little sensitivity to the red channel
    bias = np.zeros((num_classes,), dtype=np.float32)
    # 6.0 puts the winning class around 0.98, above the 0.88 in-distribution floor, so the
    # fixture represents a confident leaf rather than a borderline photo. The floor itself
    # is tested directly in test_phase3.
    bias[0] = 6.0

    graph = helper.make_graph(
        nodes=[
            helper.make_node("GlobalAveragePool", ["input"], ["pooled"]),
            helper.make_node("Flatten", ["pooled"], ["flat"], axis=1),
            helper.make_node("Gemm", ["flat", "W", "C"], ["logits"], alpha=1.0, beta=1.0),
        ],
        name="cropguard_test_graph",
        inputs=[
            helper.make_tensor_value_info("input", TensorProto.FLOAT, [1, 3, 224, 224])
        ],
        outputs=[helper.make_tensor_value_info("logits", TensorProto.FLOAT, [1, num_classes])],
        initializer=[
            numpy_helper.from_array(weights, name="W"),
            numpy_helper.from_array(bias, name="C"),
        ],
    )
    model = helper.make_model(
        graph, opset_imports=[helper.make_opsetid("", 17)], producer_name="cropguard-tests"
    )
    model.ir_version = 8
    onnx.checker.check_model(model)
    onnx.save(model, str(path))


def _prepare_model_artifacts() -> None:
    model_dir = _TMP / "model"
    model_dir.mkdir(parents=True, exist_ok=True)

    onnx_path = model_dir / "cropguard_mobilenetv3.onnx"
    _build_tiny_onnx(onnx_path, len(TEST_CLASSES))
    (model_dir / "cropguard_mobilenetv3.artifact").write_text("trained\n", encoding="utf-8")
    (model_dir / "class_names.json").write_text(
        json.dumps(TEST_CLASSES, indent=2), encoding="utf-8"
    )

    os.environ["ONNX_PATH"] = str(onnx_path)
    os.environ["CLASS_NAMES_PATH"] = str(model_dir / "class_names.json")
    os.environ["MODEL_BACKEND"] = "onnx"


_prepare_model_artifacts()
