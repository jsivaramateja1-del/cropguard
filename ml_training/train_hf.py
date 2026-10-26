#!/usr/bin/env python3
"""CropGuard — fine-tune a Hugging Face image classifier on our own label set.

Why this exists
---------------
Every plant-disease model on the Hugging Face Hub is a 38-class PlantVillage model:
tomato and potato, but no rice, and label strings that do not match ours. They cannot be
dropped into the backend, which requires an ONNX graph whose output width matches
``class_names.json`` and whose labels are our canonical ``Crop___Condition`` names.

So instead of adopting someone else's label set, this script takes a Hub model as a
*starting point* and trains a new classifier head on the same 16 classes CropGuard
serves. The result is a drop-in replacement: same ONNX contract, same label order, same
backend code, no mapping tables.

Default base model
------------------
``linkanjarad/mobilenet_v2_1.0_224-plant-disease-identification`` — already specialised
on leaf imagery, and small enough to stay fast on a 0.1 CPU free tier. Measured
alternatives:

    model                                    ONNX    peak RAM  inference (2 vCPU)
    mobilenet_v2 plant (default)            ~9 MB    ~180 MB   ~30 ms
    rodynaemad/plant-disease-dinov2-small    85 MB    274 MB   618 ms   (accuracy option)

The DINOv2 option has better features but is ~31x slower; on Render Free that is several
seconds per photo, which is a poor demo. Use it only if field accuracy disappoints:

    python train_hf.py --model rodynaemad/plant-disease-dinov2-small ...

Usage (Kaggle, GPU recommended)
-------------------------------
    !pip install -q transformers
    !python train_hf.py \
        --data-dirs "/kaggle/input/plantvillage-dataset/color" \
                    "/kaggle/input/rice-leaf-diseases:Rice" \
        --crops Tomato Potato Rice --allow-missing-classes \
        --epochs 8 --batch-size 64 --out-dir /kaggle/working/export_hf

Then upload ``cropguard_mobilenetv3.onnx`` and ``class_names.json`` from the output
directory to ``backend/model/`` exactly as before.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import numpy as np
import torch
import torch.nn as nn
from PIL import Image
from torch.utils.data import DataLoader, Dataset

# Shared with the PyTorch pipeline: identical discovery, canonicalisation and splitting,
# so a model trained here sees exactly the same data and labels as one trained there.
from cropguard_common import (
    ARTIFACT_TRAINED,
    build_class_index,
    collect_from_roots,
    load_canonical,
)
from train_kaggle import stratified_split

ONNX_FILENAME = "cropguard_mobilenetv3.onnx"
CLASS_NAMES_FILENAME = "class_names.json"


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------
class HFDataset(Dataset):
    """PIL images plus the Hugging Face processor's preprocessing.

    The processor is applied per item rather than in a transform pipeline so the model's
    own normalisation (mean/std/resize) is used — a mismatch there silently destroys
    accuracy while everything still "runs".
    """

    def __init__(self, items: Sequence[Tuple[str, int]], processor, train: bool):
        self.items = list(items)
        self.processor = processor
        self.train = train

    def __len__(self) -> int:
        return len(self.items)

    def __getitem__(self, index: int):
        path, label = self.items[index]
        try:
            with Image.open(path) as handle:
                image = handle.convert("RGB")
        except Exception:
            image = Image.new("RGB", (224, 224), (120, 140, 110))

        if self.train:
            # Light augmentation. The processor handles resize and normalisation; flipping
            # and small rotations are safe for leaf pathology and add useful invariance.
            if random.random() < 0.5:
                image = image.transpose(Image.FLIP_LEFT_RIGHT)
            if random.random() < 0.3:
                image = image.rotate(random.uniform(-15, 15), resample=Image.BILINEAR)

        pixels = self.processor(images=image, return_tensors="pt")["pixel_values"][0]
        return pixels, label


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------
def load_hf_model(model_id: str, num_classes: int):
    """Load a Hub image classifier and replace its head with ``num_classes`` outputs.

    Architectures differ in where the head lives, so this handles the common shapes
    (a Linear at ``classifier``, a Sequential whose last element is a Linear, or a
    Linear at ``head``) and fails loudly rather than silently training a frozen head.
    """
    from transformers import AutoConfig, AutoImageProcessor, AutoModelForImageClassification

    config = AutoConfig.from_pretrained(model_id)
    config.num_labels = num_classes
    # Every id2label must be present or transformers complains on save; the real label
    # order is written to class_names.json by this script.
    config.id2label = {index: f"class_{index}" for index in range(num_classes)}
    config.label2id = {value: key for key, value in config.id2label.items()}

    model = AutoModelForImageClassification.from_pretrained(
        model_id, config=config, ignore_mismatched_sizes=True
    )
    processor = AutoImageProcessor.from_pretrained(model_id)

    head = None
    if isinstance(getattr(model, "classifier", None), nn.Linear):
        head = model.classifier
        model.classifier = nn.Linear(head.in_features, num_classes)
    elif isinstance(getattr(model, "classifier", None), nn.Sequential):
        linears = [module for module in model.classifier if isinstance(module, nn.Linear)]
        if not linears:
            raise SystemExit(f"{model_id}: classifier has no Linear layer to replace")
        last = linears[-1]
        replacement = nn.Linear(last.in_features, num_classes)
        for index, module in enumerate(model.classifier):
            if module is last:
                model.classifier[index] = replacement
    elif isinstance(getattr(model, "head", None), nn.Linear):
        head = model.head
        model.head = nn.Linear(head.in_features, num_classes)
    else:
        raise SystemExit(
            f"{model_id}: could not find a classification head (looked for "
            "model.classifier as Linear or Sequential, and model.head as Linear). "
            "Add a branch for this architecture."
        )

    return model, processor


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------
@torch.no_grad()
def evaluate(model, loader, device) -> Tuple[float, float, dict, list]:
    model.eval()
    correct = total = 0
    per_class_correct: dict = {}
    per_class_total: dict = {}
    confusions: dict = {}
    confidences: List[float] = []

    for pixels, labels in loader:
        pixels = pixels.to(device)
        logits = model(pixel_values=pixels).logits
        probabilities = torch.softmax(logits, dim=1).cpu().numpy()
        predictions = probabilities.argmax(axis=1)

        for truth, prediction, row in zip(labels.tolist(), predictions.tolist(), probabilities):
            total += 1
            per_class_total[truth] = per_class_total.get(truth, 0) + 1
            confidences.append(float(row[prediction]))
            if truth == prediction:
                correct += 1
                per_class_correct[truth] = per_class_correct.get(truth, 0) + 1
            else:
                key = (truth, prediction)
                confusions[key] = confusions.get(key, 0) + 1

    accuracy = correct / total if total else 0.0
    recalls = [
        per_class_correct.get(label, 0) / count
        for label, count in per_class_total.items()
        if count
    ]
    macro_recall = float(np.mean(recalls)) if recalls else 0.0
    return accuracy, macro_recall, per_class_total, confusions


# ---------------------------------------------------------------------------
# ONNX export
# ---------------------------------------------------------------------------
def export_onnx(model, processor, num_classes: int, out_path: str, opset: int = 18) -> str:
    """Export the fine-tuned model, reusing the tested exporter from export_onnx.py."""
    sys.path.insert(0, str(Path(__file__).parent))
    from export_onnx import _check_artifact, _export_onnx  # noqa: PLC0415

    size = int(getattr(processor, "size", {}).get("height", 224) or 224)
    dummy = torch.randn(1, 3, size, size)
    model.eval()
    exporter = _export_onnx(model, dummy, out_path, opset, "auto")
    _check_artifact(out_path, num_classes)
    return exporter


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default="linkanjarad/mobilenet_v2_1.0_224-plant-disease-identification",
                        help="Hugging Face model id to fine-tune")
    parser.add_argument("--data-dirs", nargs="+", required=True)
    parser.add_argument("--crops", nargs="+", default=None)
    parser.add_argument("--allow-missing-classes", action="store_true")
    parser.add_argument("--epochs", type=int, default=8)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--lr", type=float, default=3e-4)
    parser.add_argument("--weight-decay", type=float, default=1e-4)
    parser.add_argument("--val-split", type=float, default=0.2)
    parser.add_argument("--num-workers", type=int, default=2)
    parser.add_argument("--freeze-backbone", action="store_true",
                        help="Train only the new head (faster, usually less accurate)")
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--early-stop-patience", type=int, default=3)
    parser.add_argument("--skip-onnx", action="store_true")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)

    out_dir = Path(args.out_dir or Path(__file__).parent.parent / "backend" / "model")
    out_dir.mkdir(parents=True, exist_ok=True)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print("=" * 74)
    print("CropGuard — Hugging Face fine-tune")
    print("=" * 74)
    print(f"base model : {args.model}")
    print(f"device     : {device}" + (f" ({torch.cuda.get_device_name(0)})" if device.type == "cuda" else ""))
    print(f"torch      : {torch.__version__}")

    # ---- data (identical discovery to the PyTorch pipeline) ----------------
    print("\n[1/5] Discovering datasets")
    spec = load_canonical()
    raw, notes = collect_from_roots(args.data_dirs)
    for note in notes:
        print(f"  - {note}")
    index, unmapped, warnings = build_class_index(
        raw, spec["classes"], crops=args.crops, strict=not args.allow_missing_classes
    )
    for warning in warnings:
        print(f"  ! {warning}")
    if not index:
        print("\nERROR: no supported classes found.")
        return 2

    present = sorted(index)
    label_to_index = {label: position for position, label in enumerate(present)}
    print(f"\n  {len(present)} classes, {sum(len(v) for v in index.values())} images")

    # stratified_split returns (path, label_index) pairs against the label order it is
    # given, so pass our sorted class list to keep indices aligned with class_names.json.
    train_items, val_items = stratified_split(index, present, args.val_split, args.seed)
    print(f"  train: {len(train_items)}  val: {len(val_items)}")

    # ---- model -------------------------------------------------------------
    print("\n[2/5] Loading the Hugging Face model")
    model, processor = load_hf_model(args.model, len(present))
    if args.freeze_backbone:
        for name, parameter in model.named_parameters():
            if "classifier" not in name and "head" not in name:
                parameter.requires_grad = False
        print("  backbone frozen: only the new head trains")
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    print(f"  trainable parameters: {trainable:,}")
    model.to(device)

    train_ds = HFDataset(train_items, processor, train=True)
    val_ds = HFDataset(val_items, processor, train=False)
    train_loader = DataLoader(train_ds, batch_size=args.batch_size, shuffle=True,
                              num_workers=args.num_workers, drop_last=False,
                              pin_memory=device.type == "cuda")
    val_loader = DataLoader(val_ds, batch_size=args.batch_size, shuffle=False,
                            num_workers=args.num_workers, pin_memory=device.type == "cuda")

    # ---- train -------------------------------------------------------------
    print(f"\n[3/5] Training for up to {args.epochs} epochs")
    optimizer = torch.optim.AdamW(
        [p for p in model.parameters() if p.requires_grad], lr=args.lr, weight_decay=args.weight_decay
    )
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=max(1, args.epochs))
    scaler = torch.amp.GradScaler("cuda", enabled=device.type == "cuda")
    criterion = nn.CrossEntropyLoss(label_smoothing=0.1)

    best_accuracy = 0.0
    best_state = None
    best_epoch = 0
    epochs_without_improvement = 0
    log_rows = []
    started = time.time()

    for epoch in range(1, args.epochs + 1):
        model.train()
        running_loss = 0.0
        seen = correct = 0
        epoch_started = time.time()

        for pixels, labels in train_loader:
            pixels = pixels.to(device, non_blocking=True)
            labels = labels.to(device, non_blocking=True)
            optimizer.zero_grad(set_to_none=True)
            with torch.amp.autocast("cuda", enabled=device.type == "cuda"):
                logits = model(pixel_values=pixels).logits
                loss = criterion(logits, labels)
            scaler.scale(loss).backward()
            scaler.step(optimizer)
            scaler.update()

            running_loss += float(loss) * labels.size(0)
            seen += labels.size(0)
            correct += int((logits.argmax(dim=1) == labels).sum())

        scheduler.step()
        accuracy, macro_recall, _, _ = evaluate(model, val_loader, device)
        train_loss = running_loss / max(1, seen)
        train_accuracy = correct / max(1, seen)
        elapsed = time.time() - epoch_started
        log_rows.append({
            "epoch": epoch, "train_loss": round(train_loss, 4),
            "train_acc": round(train_accuracy, 4), "val_acc": round(accuracy, 4),
            "macro_recall": round(macro_recall, 4), "seconds": round(elapsed, 1),
        })
        print(f"  epoch {epoch:2d}/{args.epochs}  loss {train_loss:.4f}  train_acc {train_accuracy:.4f}"
              f"  val_acc {accuracy:.4f}  macro_recall {macro_recall:.4f}  ({elapsed:.0f}s)")

        if accuracy > best_accuracy:
            best_accuracy = accuracy
            best_epoch = epoch
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
            epochs_without_improvement = 0
            print(f"    -> new best (val_acc {accuracy:.4f})")
        else:
            epochs_without_improvement += 1
            if epochs_without_improvement >= args.early_stop_patience:
                print(f"  early stopping: no improvement for {epochs_without_improvement} epochs")
                break

    if best_state is not None:
        model.load_state_dict(best_state)
    duration = time.time() - started

    # ---- report ------------------------------------------------------------
    print("\n[4/5] Best checkpoint summary")
    accuracy, macro_recall, per_class_total, confusions = evaluate(model, val_loader, device)
    print(f"  best epoch        : {best_epoch}")
    print(f"  validation acc    : {accuracy:.4f}")
    print(f"  macro recall      : {macro_recall:.4f}")
    print(f"  total time        : {duration/60:.1f} min")
    print("\n  per-class validation accuracy")
    for position, label in enumerate(present):
        # Counts are keyed by label index, which is why they must be looked up by position.
        count = per_class_total.get(position, 0)
        wrong = sum(v for (truth, _), v in confusions.items() if truth == position)
        share = (count - wrong) / count if count else 0.0
        print(f"    {label:<44} {share:.4f}  (n={count})")
    if confusions:
        print("\n  most frequent confusions")
        for (truth, prediction), count in sorted(confusions.items(), key=lambda kv: -kv[1])[:5]:
            print(f"    {present[truth]} -> {present[prediction]}  ({count}x)")

    # ---- export ------------------------------------------------------------
    print("\n[5/5] Exporting")
    class_names_path = out_dir / CLASS_NAMES_FILENAME
    class_names_path.write_text(json.dumps(present, indent=2) + "\n", encoding="utf-8")
    print(f"  class names : {class_names_path}")

    checkpoint_path = out_dir / "cropguard_hf.pth"
    torch.save(
        {
            "model_id": args.model,
            "state_dict": model.state_dict(),
            "class_names": present,
            "val_accuracy": accuracy,
            "macro_recall": macro_recall,
            "trained_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "artifact_type": ARTIFACT_TRAINED,
        },
        checkpoint_path,
    )
    print(f"  checkpoint  : {checkpoint_path}")

    if not args.skip_onnx:
        onnx_path = out_dir / ONNX_FILENAME
        exporter = export_onnx(model, processor, len(present), str(onnx_path))
        size_mb = onnx_path.stat().st_size / (1024 * 1024)
        print(f"  onnx        : {onnx_path} ({size_mb:.1f} MB, {exporter})")
        marker = out_dir / "cropguard_mobilenetv3.artifact"
        marker.write_text(ARTIFACT_TRAINED + "\n", encoding="utf-8")
        (out_dir / "training_metrics_hf.json").write_text(
            json.dumps(
                {
                    "model_id": args.model,
                    "class_names": present,
                    "val_accuracy": round(accuracy, 4),
                    "macro_recall": round(macro_recall, 4),
                    "epochs": log_rows,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    (out_dir / "training_log_hf.json").write_text(json.dumps(log_rows, indent=2) + "\n", encoding="utf-8")

    print("\nUpload BOTH files to backend/model/ and let Render redeploy:")
    print(f"  {out_dir / ONNX_FILENAME}")
    print(f"  {class_names_path}")
    print("\nThe backend needs no code change: it loads whatever ONNX + class_names pair")
    print("is present. Keep the current model if this one does not beat it — the previous")
    print("MobileNetV3 scored 0.9976 validation and 36/36 on labelled photos.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
