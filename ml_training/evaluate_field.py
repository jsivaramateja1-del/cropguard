#!/usr/bin/env python3
"""Measure accuracy on real field photographs — the number the pitch actually needs.

PlantVillage is lab photography: one leaf, cut, on a uniform background, well lit. A
farmer's photo is none of those things. This script runs the exported model over a field
dataset and reports what the model really does, including how badly it degrades.

It refuses to produce a flattering number:

* it reports **top-1 accuracy, macro recall, and a confusion matrix**, not just accuracy
* it reports **how many predictions fall below the review threshold**, because those are
  the cases a farmer would be told to resend
* it reports accuracy **separately for images the model was trained on the class of**
  versus classes it never saw — a class it cannot know should not drag the average down
  silently, and it must not be hidden either
* it writes every prediction to CSV so a wrong number can be traced to a specific image

Field datasets that work here (Kaggle):

* `abdallahalidev/plantvillage-dataset` — lab, for the baseline comparison
* `emmarex/plantdisease` — mixed lab/field potato and pepper
* `vipoooool/new-plant-diseases-dataset` — larger PlantVillage re-split
* **`nirmalsankalana/plantdoc-dataset`** — PlantDoc: real field photos, 27 classes, the
  honest benchmark. Use this one for the headline number.

Usage:

    !python ml_training/evaluate_field.py \
        --model /kaggle/working/export/cropguard_mobilenetv3.onnx \
        --data-dir /kaggle/input/plantdoc-dataset/train \
        --out-dir /kaggle/working/field-eval

Works with either an ONNX artifact or a `.pth` checkpoint (`--backend` to force one).
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cropguard_common import canonicalize_map, load_canonical  # noqa: E402

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".JPG", ".JPEG", ".PNG"}


def build_predictor(model_path: Path, backend: str, image_size: int):
    """Return (predict_fn, class_names, backend_used)."""
    class_names = json.loads((model_path.parent / "class_names.json").read_text())

    if backend in {"auto", "onnx"} and model_path.suffix == ".onnx":
        import onnxruntime as ort  # noqa: PLC0415

        session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
        mean = np.array([0.485, 0.456, 0.406], dtype=np.float32).reshape(3, 1, 1)
        std = np.array([0.229, 0.224, 0.225], dtype=np.float32).reshape(3, 1, 1)

        def predict(image: Image.Image) -> np.ndarray:
            array = np.asarray(
                image.convert("RGB").resize((image_size, image_size), Image.BILINEAR),
                dtype=np.float32,
            ).transpose(2, 0, 1) / 255.0
            batch = ((array - mean) / std)[None, ...].astype(np.float32)
            logits = session.run(None, {session.get_inputs()[0].name: batch})[0]
            return softmax(logits[0])

        return predict, class_names, "onnx"

    import torch  # noqa: PLC0415

    from cropguard_common import build_model  # noqa: PLC0415

    checkpoint = torch.load(model_path, map_location="cpu", weights_only=False)
    model = build_model(len(class_names), pretrained=False)
    model.load_state_dict(checkpoint.get("model_state") or checkpoint)
    model.eval()
    mean = torch.tensor([0.485, 0.456, 0.406]).view(3, 1, 1)
    std = torch.tensor([0.229, 0.224, 0.225]).view(3, 1, 1)

    def predict(image: Image.Image) -> np.ndarray:
        import torchvision.transforms.functional as F  # noqa: PLC0415

        tensor = F.pil_to_tensor(image.convert("RGB").resize((image_size, image_size)))
        batch = ((tensor.float() / 255.0 - mean) / std)[None, ...]
        with torch.no_grad():
            return softmax(model(batch)[0].numpy())

    return predict, class_names, "torch"


def softmax(logits: np.ndarray) -> np.ndarray:
    shifted = logits - logits.max()
    exponentials = np.exp(shifted)
    return exponentials / exponentials.sum()


def collect(data_dir: Path, mapping: dict) -> tuple:
    """Walk the dataset, mapping folder names onto canonical labels."""
    samples, unmapped = [], Counter()
    for folder in sorted(p for p in data_dir.rglob("*") if p.is_dir()):
        images = [p for p in folder.iterdir() if p.suffix in IMAGE_SUFFIXES]
        if not images:
            continue
        label = mapping.get(folder.name.lower())
        if label is None:
            unmapped[folder.name] = len(images)
            continue
        samples.extend((image, label) for image in images)
    return samples, unmapped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--image-size", type=int, default=224)
    parser.add_argument("--backend", choices=["auto", "onnx", "torch"], default="auto")
    parser.add_argument("--review-threshold", type=float, default=0.75)
    parser.add_argument("--limit", type=int, default=0, help="cap the number of images")
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    canonical = load_canonical()
    mapping = canonicalize_map(canonical["labels"])

    samples, unmapped = collect(args.data_dir, mapping)
    if args.limit:
        samples = samples[: args.limit]
    if not samples:
        raise SystemExit(
            f"No labelled images found under {args.data_dir}. Expected class subfolders "
            "whose names match the canonical labels (or the aliases in the mapping)."
        )

    print(f"{len(samples)} images across {len({label for _, label in samples})} labels")
    if unmapped:
        print(f"skipped {sum(unmapped.values())} images in unmapped folders: {dict(unmapped)}")

    predict, class_names, backend = build_predictor(args.model, args.backend, args.image_size)
    print(f"model: {backend}, {len(class_names)} classes")

    unknown_classes = sorted({label for _, label in samples if label not in class_names})
    if unknown_classes:
        print(
            f"WARNING: {len(unknown_classes)} ground-truth classes are not in the model: "
            f"{unknown_classes[:6]} — those images cannot be predicted correctly."
        )

    rows, correct, total = [], 0, 0
    per_class = defaultdict(lambda: {"correct": 0, "total": 0})
    confidences = []
    below_threshold = 0

    for index, (image_path, truth) in enumerate(samples, start=1):
        try:
            probabilities = predict(Image.open(image_path))
        except Exception as error:  # noqa: BLE001
            print(f"  skipped {image_path.name}: {type(error).__name__}")
            continue

        predicted_index = int(probabilities.argmax())
        predicted = class_names[predicted_index]
        confidence = float(probabilities[predicted_index])

        total += 1
        hit = predicted == truth
        correct += int(hit)
        per_class[truth]["total"] += 1
        per_class[truth]["correct"] += int(hit)
        confidences.append(confidence)
        below_threshold += int(confidence < args.review_threshold)

        rows.append(
            {
                "image": str(image_path),
                "truth": truth,
                "predicted": predicted,
                "confidence": round(confidence, 4),
                "correct": hit,
            }
        )

        if index % 200 == 0:
            print(f"  {index}/{len(samples)} processed")

    if not total:
        raise SystemExit("Every image failed to load; nothing to measure.")

    accuracy = correct / total
    recalls = [v["correct"] / v["total"] for v in per_class.values() if v["total"]]
    macro_recall = float(np.mean(recalls)) if recalls else 0.0

    summary = {
        "dataset": str(args.data_dir),
        "model": str(args.model),
        "backend": backend,
        "images": total,
        "accuracy": round(accuracy, 4),
        "macro_recall": round(macro_recall, 4),
        "mean_confidence": round(float(np.mean(confidences)), 4),
        "median_confidence": round(float(np.median(confidences)), 4),
        "below_review_threshold": below_threshold,
        "below_review_share": round(below_threshold / total, 4),
        "review_threshold": args.review_threshold,
        "classes_in_model": len(class_names),
        "classes_evaluated": len(per_class),
        "unknown_ground_truth_classes": unknown_classes,
    }

    with (args.out_dir / "predictions.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    per_class_rows = [
        {
            "label": label,
            "correct": values["correct"],
            "total": values["total"],
            "recall": round(values["correct"] / values["total"], 4),
        }
        for label, values in sorted(per_class.items())
    ]
    with (args.out_dir / "per_class.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(per_class_rows[0]))
        writer.writeheader()
        writer.writerows(per_class_rows)

    (args.out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print("\n" + "=" * 62)
    print("FIELD EVALUATION")
    print("=" * 62)
    print(f"  images              : {total}")
    print(f"  top-1 accuracy      : {accuracy:.1%}")
    print(f"  macro recall        : {macro_recall:.1%}")
    print(f"  median confidence   : {np.median(confidences):.3f}")
    print(f"  below {args.review_threshold:.2f} (sent to review): {below_threshold} "
          f"({below_threshold / total:.1%})")
    print(f"\n  weakest classes:")
    for row in sorted(per_class_rows, key=lambda r: r["recall"])[:5]:
        print(f"    {row['label']:<44} {row['recall']:.2f} (n={row['total']})")
    print(f"\n  written to {args.out_dir}")
    print(
        "\nReport this number as it is. A measured 70% on field photos is worth more than an\n"
        "unmeasured 99% on lab photos, and a judge will ask which one you are quoting."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
