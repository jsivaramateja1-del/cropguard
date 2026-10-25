"""Shared helpers for the CropGuard training pipeline.

Used by:
  * ml_training/train_kaggle.py          -> real GPU training on Kaggle
  * ml_training/generate_starter_model.py -> development artifact for backend testing

Keeping the label canonicalisation, model factory and checkpoint format in one
place guarantees that the model the backend loads always matches the model the
training script exported.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import torch
import torch.nn as nn
from torchvision import transforms
from torchvision.models import MobileNet_V3_Large_Weights, mobilenet_v3_large

MODEL_NAME = "mobilenet_v3_large"
CHECKPOINT_FILENAME = "cropguard_mobilenetv3.pth"
CLASS_NAMES_FILENAME = "class_names.json"
CANONICAL_FILENAME = "class_names_canonical.json"

IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".bmp",
    ".webp",
    ".tif",
    ".tiff",
    ".jfif",
}

# Metadata keys written into every checkpoint. The backend reads these to decide
# whether it is holding a real trained model or a development artifact.
META_ARTIFACT_TYPE = "artifact_type"
ARTIFACT_TRAINED = "trained"
ARTIFACT_DEVELOPMENT = "development_untrained"

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------------------
# Canonical class list
# ---------------------------------------------------------------------------
def canonical_path(path: Optional[str] = None) -> str:
    return path or os.path.join(_THIS_DIR, CANONICAL_FILENAME)


def load_canonical(path: Optional[str] = None) -> dict:
    """Load class_names_canonical.json (label list + preprocessing contract)."""
    resolved = canonical_path(path)
    if not os.path.isfile(resolved):
        raise FileNotFoundError(
            f"Canonical class list not found at {resolved}. "
            f"Restore {CANONICAL_FILENAME} from the repository."
        )
    with open(resolved, "r", encoding="utf-8") as handle:
        spec = json.load(handle)

    classes = spec.get("classes") or []
    if not classes:
        raise ValueError(f"{resolved} contains no classes.")
    if len(set(classes)) != len(classes):
        raise ValueError(f"{resolved} contains duplicate class labels.")

    spec["classes"] = list(classes)
    spec.setdefault("image_size", 224)
    spec.setdefault("normalization", {"mean": [0.485, 0.456, 0.406], "std": [0.229, 0.224, 0.225]})
    return spec


def _label_key(label: str) -> str:
    """Collapse a folder name to a comparison key.

    Handles the naming variants seen across public agricultural datasets:
      "Tomato___Early_blight"  -> "tomatoearlyblight"
      "Tomato-Early blight"    -> "tomatoearlyblight"
      "tomato_early_blight"    -> "tomatoearlyblight"
    """
    return re.sub(r"[^a-z0-9]", "", label.lower())


def canonicalize_map(classes: Sequence[str]) -> Dict[str, str]:
    """Build {normalised key -> canonical label} for fast dataset mapping."""
    return {_label_key(name): name for name in classes}


def canonicalize(label: str, mapping: Dict[str, str]) -> Optional[str]:
    return mapping.get(_label_key(label))


def crop_of(label: str) -> str:
    """'Tomato___Early_blight' -> 'Tomato'."""
    return label.split("___", 1)[0]


def condition_of(label: str) -> str:
    """'Tomato___Early_blight' -> 'Early_blight'."""
    parts = label.split("___", 1)
    return parts[1] if len(parts) > 1 else label


# ---------------------------------------------------------------------------
# Dataset discovery
# ---------------------------------------------------------------------------
def is_image_file(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in IMAGE_EXTENSIONS


def describe_missing_root(root: str, max_matches: int = 5, max_depth: int = 4) -> str:
    """Explain why ``root`` is missing and suggest directories that do exist.

    Kaggle mount paths are easy to get wrong (the slug is not the display title,
    and a zip's top-level folder is sometimes dropped), so a bare
    'No such file or directory' wastes a GPU session. This walks up to the
    nearest existing ancestor, lists what is there, and looks for directories
    with the same name a few levels down.
    """
    absolute = os.path.abspath(root)
    lines = [f"Dataset directory does not exist: {root}"]

    ancestor = absolute
    while not os.path.isdir(ancestor) and os.path.dirname(ancestor) != ancestor:
        ancestor = os.path.dirname(ancestor)

    if not os.path.isdir(ancestor):
        return "\n".join(lines)

    lines.append(f"Nearest existing directory: {ancestor}")
    try:
        entries = sorted(os.listdir(ancestor))[:12]
    except OSError:
        entries = []
    if entries:
        lines.append("It contains: " + ", ".join(entries))

    target = os.path.basename(os.path.normpath(absolute)).lower()
    if target:
        matches: List[str] = []
        base_depth = ancestor.rstrip(os.sep).count(os.sep)
        for current, subdirs, _files in os.walk(ancestor):
            if current.rstrip(os.sep).count(os.sep) - base_depth >= max_depth:
                subdirs[:] = []
            if os.path.basename(current).lower() == target and current != absolute:
                matches.append(current)
                if len(matches) >= max_matches:
                    break
        if matches:
            lines.append("Directories with that name that do exist:")
            lines.extend(f"  {match}" for match in matches)

    lines.append(
        "Hint: on Kaggle the mount is named after the dataset slug, e.g. "
        "/kaggle/input/plantvillage-dataset, and a zip's wrapper folder is "
        "sometimes dropped. List /kaggle/input to check."
    )
    return "\n".join(lines)


def discover_class_dirs(root: str) -> Dict[str, List[str]]:
    """Walk ``root`` and treat every image-bearing directory as one class.

    Handles the nested layouts common to Kaggle mirrors, e.g.
      PlantVillage/plantvillage dataset/color/Tomato___Early_blight/*.JPG
    by only registering directories that directly contain image files.
    """
    if not os.path.isdir(root):
        raise NotADirectoryError(describe_missing_root(root))

    found: Dict[str, List[str]] = {}
    for current, _subdirs, filenames in os.walk(root):
        images = [os.path.join(current, name) for name in filenames if is_image_file(name)]
        if not images:
            continue
        class_name = os.path.basename(current.rstrip(os.sep))
        if not class_name:
            continue
        found.setdefault(class_name, []).extend(images)

    for name in found:
        found[name].sort()
    return found


KNOWN_CROPS = ("Tomato", "Rice", "Potato", "Wheat")


def parse_root_spec(spec: str) -> Tuple[str, Optional[str]]:
    """Split a dataset root into (path, crop hint).

    Many public datasets name their class folders without the crop, e.g. the
    rice dataset uses "Bacterial leaf blight" rather than
    "Rice___Bacterial_leaf_blight". A trailing ``:Crop`` tells the loader which
    crop those folders belong to:

        /kaggle/input/rice-leaf-diseases:Rice   ->  (/kaggle/input/rice-leaf-diseases, "Rice")

    Only a recognised crop name is treated as a hint, so ordinary paths (and
    paths containing colons) are left untouched.
    """
    head, separator, tail = spec.rpartition(":")
    if separator and tail.strip().lower() in {crop.lower() for crop in KNOWN_CROPS}:
        return head, tail.strip().capitalize()
    return spec, None


def collect_from_roots(specs: Iterable[str]) -> Tuple[Dict[Tuple[Optional[str], str], List[str]], List[str]]:
    """Merge several dataset roots into {(crop hint, raw class name): [image paths]}.

    The crop hint is part of the key so that "Brown spot" from a rice dataset and
    a hypothetical "Brown spot" from a wheat dataset stay distinct.
    Returns the merged mapping plus a human-readable provenance note per root.
    """
    merged: Dict[Tuple[Optional[str], str], List[str]] = {}
    notes: List[str] = []
    for spec in specs:
        root, crop_hint = parse_root_spec(spec)
        per_root = discover_class_dirs(root)
        total = sum(len(paths) for paths in per_root.values())
        suffix = f" [crop hint: {crop_hint}]" if crop_hint else ""
        notes.append(f"{root}: {len(per_root)} class dirs, {total} images{suffix}")

        # PlantVillage ships colour, greyscale and pre-segmented copies of the same
        # leaves. Because classes are keyed by directory name, pointing at the
        # dataset root would merge all three variants into one class and train on
        # grey duplicates. Warn rather than silently doing that.
        variants = sorted(
            {
                entry.lower()
                for entry in os.listdir(root)
                if os.path.isdir(os.path.join(root, entry))
                and entry.lower() in {"color", "colour", "grayscale", "greyscale", "segmented"}
            }
        )
        if len(variants) > 1:
            notes.append(
                f"  ! {root} contains multiple image variants ({', '.join(variants)}). "
                "Point --data-dirs at the color/ subdirectory so greyscale and "
                "segmented copies are not merged into the same classes."
            )

        for class_name, paths in per_root.items():
            merged.setdefault((crop_hint, class_name), []).extend(paths)
    return merged, notes


def build_class_index(
    raw: Dict[Tuple[Optional[str], str], List[str]],
    classes: Sequence[str],
    crops: Optional[Sequence[str]] = None,
    strict: bool = True,
) -> Tuple[Dict[str, List[str]], List[str], List[str]]:
    """Map raw dataset folder names onto the canonical label list.

    A folder name is first matched as-is. If that fails and the root carried a
    crop hint, it is retried as ``<Crop>___<folder>``.
    Returns (canonical_label -> image paths, unmapped raw names, warnings).
    """
    mapping = canonicalize_map(classes)
    wanted_crops = {c.lower() for c in crops} if crops else None

    index: Dict[str, List[str]] = {}
    unmapped: List[str] = []
    warnings: List[str] = []
    hint_would_help = False

    for (crop_hint, raw_name), paths in raw.items():
        label = canonicalize(raw_name, mapping)
        if label is None and crop_hint:
            label = canonicalize(f"{crop_hint}___{raw_name}", mapping)
        if label is None:
            unmapped.append(raw_name if not crop_hint else f"{raw_name} [{crop_hint} set]")
            if not crop_hint and any(
                canonicalize(f"{crop}___{raw_name}", mapping) for crop in KNOWN_CROPS
            ):
                hint_would_help = True
            continue
        if wanted_crops and crop_of(label).lower() not in wanted_crops:
            continue
        index.setdefault(label, []).extend(paths)

    for label in index:
        index[label] = sorted(set(index[label]))

    if unmapped:
        message = (
            f"{len(unmapped)} dataset folder(s) do not map to a supported class and were skipped: "
            + ", ".join(sorted(unmapped)[:12])
            + (" ..." if len(unmapped) > 12 else "")
        )
        if hint_would_help:
            message += (
                "\n    Some of these match a supported class once the crop is known. "
                "Append the crop to that root, e.g. /path/to/dataset:Rice"
            )
        warnings.append(message)

    if strict:
        missing = [label for label in classes if label not in index]
        if wanted_crops:
            missing = [m for m in missing if crop_of(m).lower() in wanted_crops]
        if missing:
            raise SystemExit(
                "Training aborted: no images found for these supported classes:\n  "
                + "\n  ".join(missing)
                + "\n\nEither add the matching dataset roots, drop the crop with --crops, "
                "or re-run with --allow-missing-classes to train on the classes that do exist."
            )

    return index, unmapped, warnings


# ---------------------------------------------------------------------------
# Model factory
# ---------------------------------------------------------------------------
def build_model(
    num_classes: int,
    pretrained: bool = True,
    dropout: float = 0.2,
    allow_download: bool = True,
) -> nn.Module:
    """MobileNetV3-Large with the ImageNet classifier replaced for ``num_classes``.

    If ``pretrained`` is requested but the weights cannot be fetched (offline
    notebook, blocked DNS, no cache), the model falls back to random
    initialisation with a loud warning instead of raising. Training still runs;
    it simply needs many more epochs to converge, and the caller can tell from
    the printed warning that the run is not the intended one.

    Pass ``allow_download=False`` to skip the download attempt entirely (useful
    when you know the environment is offline and do not want to wait for the
    network timeout).
    """
    if num_classes < 2:
        raise ValueError("num_classes must be >= 2")

    model = None
    backbone_pretrained = False
    if pretrained and allow_download:
        weights = MobileNet_V3_Large_Weights.IMAGENET1K_V2
        try:
            model = mobilenet_v3_large(weights=weights)
            backbone_pretrained = True
            print("  backbone     : ImageNet-1K V2 pretrained weights loaded")
        except Exception as error:  # URLError, gaierror, RuntimeError, HTTP errors...
            print("  ! could not load ImageNet weights:", f"{type(error).__name__}: {error}")
            print("  ! falling back to RANDOM initialisation for this run.")
            print("    Training from scratch needs far more epochs than fine-tuning.")
            print("    Fix the network (Kaggle: Settings -> Internet -> On), or pre-cache the")
            print("    weights, then re-run. To silence this attempt use --no-pretrained.")
            model = None

    if model is None:
        if pretrained and not allow_download:
            print("  backbone     : random init (download disabled)")
        elif not pretrained:
            print("  backbone     : random init (--no-pretrained)")
        else:
            print("  backbone     : random init (pretrained weights unavailable)")
        model = mobilenet_v3_large(weights=None)

    in_features = model.classifier[-1].in_features
    model.classifier[-1] = nn.Linear(in_features, num_classes)

    if dropout is not None:
        # The stock head is Linear -> Hardswish -> Dropout -> Linear.
        # Replace the dropout probability so it is explicit and tunable.
        for module in model.classifier.modules():
            if isinstance(module, nn.Dropout):
                module.p = float(dropout)

    # Reported by the training script so the exported metrics never claim a
    # pretrained backbone that was not actually used.
    model.cropguard_pretrained = backbone_pretrained
    return model


def count_parameters(model: nn.Module) -> int:
    return sum(p.numel() for p in model.parameters() if p.requires_grad)


# ---------------------------------------------------------------------------
# Transforms
# ---------------------------------------------------------------------------
def train_transform(image_size: int, mean: Sequence[float], std: Sequence[float]) -> transforms.Compose:
    return transforms.Compose(
        [
            transforms.RandomResizedCrop(image_size, scale=(0.65, 1.0), ratio=(0.8, 1.25)),
            transforms.RandomHorizontalFlip(p=0.5),
            transforms.RandomVerticalFlip(p=0.2),
            transforms.RandomRotation(degrees=20),
            transforms.ColorJitter(brightness=0.25, contrast=0.25, saturation=0.2, hue=0.04),
            transforms.ToTensor(),
            transforms.Normalize(mean=list(mean), std=list(std)),
        ]
    )


def eval_transform(image_size: int, mean: Sequence[float], std: Sequence[float]) -> transforms.Compose:
    resize_to = int(round(image_size * 256 / 224))
    return transforms.Compose(
        [
            transforms.Resize(resize_to),
            transforms.CenterCrop(image_size),
            transforms.ToTensor(),
            transforms.Normalize(mean=list(mean), std=list(std)),
        ]
    )


# ---------------------------------------------------------------------------
# Checkpoint I/O
# ---------------------------------------------------------------------------
def save_checkpoint(
    path: str,
    model: nn.Module,
    class_names: Sequence[str],
    spec: dict,
    artifact_type: str,
    extra: Optional[dict] = None,
) -> str:
    payload = {
        "model_name": MODEL_NAME,
        "num_classes": len(class_names),
        "class_names": list(class_names),
        "image_size": int(spec["image_size"]),
        "normalization": spec["normalization"],
        "artifact_type": artifact_type,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "state_dict": model.state_dict(),
    }
    if extra:
        payload.update(extra)

    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    torch.save(payload, path)
    return path


def save_class_names(path: str, class_names: Sequence[str]) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(list(class_names), handle, indent=2)
        handle.write("\n")
    return path


def load_checkpoint(path: str, device: str = "cpu") -> dict:
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Checkpoint not found: {path}")
    # The checkpoint is produced by this repository and contains only tensors,
    # plain containers and strings, so weights_only loading is safe here.
    payload = torch.load(path, map_location=device, weights_only=True)
    for key in ("state_dict", "class_names", "image_size", "normalization"):
        if key not in payload:
            raise ValueError(f"Checkpoint {path} is missing required key '{key}'.")
    return payload


def checkpoint_to_model(payload: dict, device: str = "cpu") -> nn.Module:
    model = build_model(len(payload["class_names"]), pretrained=False)
    model.load_state_dict(payload["state_dict"])
    model.to(device)
    model.eval()
    return model
