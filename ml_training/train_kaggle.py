#!/usr/bin/env python3
"""CropGuard — MobileNetV3-Large crop-disease training pipeline (Kaggle / GPU).

Trains a 21-class crop-disease classifier for Tomato, Rice, Potato and Wheat on
public agricultural datasets, then exports the two artifacts the backend loads:

    backend/model/cropguard_mobilenetv3.pth
    backend/model/class_names.json

Typical Kaggle use (GPU T4/P100, Internet ON):

    !git clone https://github.com/<you>/cropguard.git
    %cd cropguard/ml_training
    !python train_kaggle.py \
        --data-dirs /kaggle/input/plantvillage-dataset/plantvillage\\ dataset/color \
                     /kaggle/input/rice-leaf-diseases \
                     /kaggle/input/wheat-leaf-dataset \
        --epochs 12 --batch-size 64 --out-dir /kaggle/working/export

Everything this script writes is a real artifact of a real run: no synthetic
metrics, no fabricated classes. If a class has no images it aborts (unless
--allow-missing-classes is given) rather than silently training on a subset.

The full label list lives in class_names_canonical.json.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Dict, List, Sequence, Tuple

import numpy as np
import torch
import torch.nn as nn
from PIL import Image, UnidentifiedImageError
from torch.utils.data import DataLoader, Dataset
from torchvision import transforms

from cropguard_common import (
    ARTIFACT_TRAINED,
    CHECKPOINT_FILENAME,
    CLASS_NAMES_FILENAME,
    build_class_index,
    build_model,
    checkpoint_to_model,
    collect_from_roots,
    condition_of,
    count_parameters,
    crop_of,
    eval_transform,
    load_canonical,
    load_checkpoint,
    save_checkpoint,
    save_class_names,
    train_transform,
)

DEFAULT_CROPS = ("Tomato", "Rice", "Potato", "Wheat")


# ---------------------------------------------------------------------------
# Reproducibility
# ---------------------------------------------------------------------------
def seed_everything(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    os.environ["PYTHONHASHSEED"] = str(seed)


# ---------------------------------------------------------------------------
# Dataset
# ---------------------------------------------------------------------------
class LeafDataset(Dataset):
    """(image path, integer label) pairs with a torchvision transform."""

    def __init__(self, samples: Sequence[Tuple[str, int]], transform: transforms.Compose):
        self.samples = list(samples)
        self.transform = transform
        self.skipped: List[str] = []

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, index: int):
        path, label = self.samples[index]
        try:
            with Image.open(path) as handle:
                image = handle.convert("RGB")
            return self.transform(image), label
        except (UnidentifiedImageError, OSError):
            # Corrupt/truncated file: substitute a black frame rather than
            # crashing a long Kaggle run, and record it for the final report.
            self.skipped.append(path)
            fallback = Image.new("RGB", (224, 224), (0, 0, 0))
            return self.transform(fallback), label


def stratified_split(
    index: Dict[str, List[str]],
    class_names: Sequence[str],
    val_split: float,
    seed: int,
) -> Tuple[List[Tuple[str, int]], List[Tuple[str, int]]]:
    """Deterministic per-class split so every class appears in validation."""
    rng = random.Random(seed)
    train: List[Tuple[str, int]] = []
    val: List[Tuple[str, int]] = []

    for label_id, label in enumerate(class_names):
        paths = list(index.get(label, []))
        if not paths:
            continue
        rng.shuffle(paths)

        if len(paths) == 1:
            train.append((paths[0], label_id))
            continue

        n_val = int(round(len(paths) * val_split))
        n_val = max(1, min(n_val, len(paths) - 1))

        val.extend((p, label_id) for p in paths[:n_val])
        train.extend((p, label_id) for p in paths[n_val:])

    rng.shuffle(train)
    rng.shuffle(val)
    return train, val


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------
@torch.no_grad()
def evaluate(
    model: nn.Module,
    loader: DataLoader,
    device: torch.device,
    class_names: Sequence[str],
    criterion: nn.Module,
) -> dict:
    model.eval()
    total_loss = 0.0
    total = 0
    correct = 0
    per_class_total = Counter()
    per_class_correct = Counter()
    confusion = defaultdict(Counter)

    for images, labels in loader:
        images = images.to(device, non_blocking=True)
        labels = labels.to(device, non_blocking=True)
        logits = model(images)
        loss = criterion(logits, labels)

        total_loss += float(loss.item()) * labels.size(0)
        predictions = logits.argmax(dim=1)
        total += labels.size(0)
        correct += int((predictions == labels).sum().item())

        for truth, predicted in zip(labels.tolist(), predictions.tolist()):
            per_class_total[truth] += 1
            if truth == predicted:
                per_class_correct[truth] += 1
            else:
                confusion[truth][predicted] += 1

    per_class_accuracy = {
        class_names[label_id]: round(per_class_correct[label_id] / per_class_total[label_id], 4)
        for label_id in sorted(per_class_total)
    }
    top_confusions = []
    for truth, counter in confusion.items():
        predicted_id, hits = counter.most_common(1)[0]
        top_confusions.append(
            {
                "true": class_names[truth],
                "predicted_as": class_names[predicted_id],
                "count": hits,
            }
        )
    top_confusions.sort(key=lambda item: item["count"], reverse=True)

    recalls = list(per_class_accuracy.values())
    return {
        "val_loss": round(total_loss / total, 4) if total else None,
        "val_accuracy": round(correct / total, 4) if total else None,
        "macro_recall": round(float(np.mean(recalls)), 4) if recalls else None,
        "val_samples": total,
        "per_class_accuracy": per_class_accuracy,
        "top_confusions": top_confusions[:8],
    }


# ---------------------------------------------------------------------------
# Inference helper (--predict)
# ---------------------------------------------------------------------------
def predict_image(
    checkpoint_path: str,
    image_path: str,
    top_k: int = 5,
    device_name: str = "cpu",
) -> None:
    device = torch.device(device_name)
    payload = load_checkpoint(checkpoint_path, device=str(device))
    class_names = payload["class_names"]
    model = checkpoint_to_model(payload, device=str(device))

    mean = payload["normalization"]["mean"]
    std = payload["normalization"]["std"]
    transform = eval_transform(int(payload["image_size"]), mean, std)

    with Image.open(image_path) as handle:
        image = handle.convert("RGB")
    tensor = transform(image).unsqueeze(0).to(device)

    with torch.no_grad():
        probabilities = torch.softmax(model(tensor), dim=1)[0]

    k = min(top_k, len(class_names))
    scores, indices = probabilities.topk(k)

    print(f"\nCheckpoint : {checkpoint_path}")
    print(f"Artifact   : {payload.get('artifact_type', 'unknown')}")
    if payload.get("artifact_type") != ARTIFACT_TRAINED:
        print("WARNING    : this is a development artifact — the scores below are")
        print("             not meaningful disease predictions.")
    print(f"Image      : {image_path}")
    print(f"{'class':<48}{'probability':>12}")
    for score, index in zip(scores.tolist(), indices.tolist()):
        print(f"{class_names[index]:<48}{score:>11.4f}")
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    here = os.path.dirname(os.path.abspath(__file__))
    default_out = os.path.normpath(os.path.join(here, "..", "backend", "model"))

    parser = argparse.ArgumentParser(
        description="Train CropGuard's MobileNetV3-Large crop-disease classifier.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--data-dirs",
        nargs="+",
        default=[],
        help=(
            "One or more dataset roots. Each image-bearing subdirectory is treated as a class. "
            "Append ':Crop' when the folders omit the crop name, e.g. "
            "/kaggle/input/rice-leaf-diseases:Rice"
        ),
    )
    parser.add_argument(
        "--crops",
        nargs="+",
        default=list(DEFAULT_CROPS),
        help="Restrict training to these crops.",
    )
    parser.add_argument("--epochs", type=int, default=12)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--lr", type=float, default=3e-4)
    parser.add_argument("--weight-decay", type=float, default=1e-4)
    parser.add_argument("--val-split", type=float, default=0.2)
    parser.add_argument("--label-smoothing", type=float, default=0.1)
    parser.add_argument("--dropout", type=float, default=0.2)
    parser.add_argument("--early-stop-patience", type=int, default=4)
    parser.add_argument("--num-workers", type=int, default=2)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--out-dir",
        default=default_out,
        help="Directory for cropguard_mobilenetv3.pth and class_names.json.",
    )
    parser.add_argument(
        "--no-pretrained",
        action="store_true",
        help="Start from random weights instead of ImageNet weights (not recommended).",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help=(
            "Do not attempt to download ImageNet weights. Use in offline notebooks; "
            "the run then trains from random init unless the weights are already cached."
        ),
    )
    parser.add_argument(
        "--allow-missing-classes",
        action="store_true",
        help="Train on whatever classes exist instead of aborting when one is missing.",
    )
    parser.add_argument(
        "--class-weights",
        action="store_true",
        help="Weight the loss by inverse class frequency (helps long-tailed datasets).",
    )
    parser.add_argument(
        "--inspect-only",
        action="store_true",
        help="Report dataset discovery and class coverage, then exit without training.",
    )
    parser.add_argument(
        "--predict",
        default=None,
        help="Run inference on a single image using --checkpoint and exit.",
    )
    parser.add_argument(
        "--checkpoint",
        default=None,
        help="Checkpoint to use with --predict (defaults to <out-dir>/cropguard_mobilenetv3.pth).",
    )
    parser.add_argument("--top-k", type=int, default=5)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if args.predict:
        checkpoint = args.checkpoint or os.path.join(args.out_dir, CHECKPOINT_FILENAME)
        predict_image(checkpoint, args.predict, top_k=args.top_k, device_name=str(device))
        return 0

    seed_everything(args.seed)
    spec = load_canonical()
    all_classes = spec["classes"]
    image_size = int(spec["image_size"])
    mean = spec["normalization"]["mean"]
    std = spec["normalization"]["std"]

    print("=" * 74)
    print("CropGuard training pipeline")
    print("=" * 74)
    print(f"torch          : {torch.__version__}")
    print(f"device         : {device}" + (f" ({torch.cuda.get_device_name(0)})" if device.type == "cuda" else ""))
    if device.type != "cuda":
        print("NOTE           : no GPU detected. Training will be slow; Kaggle/Colab GPU recommended.")
    print(f"supported      : {len(all_classes)} classes across {', '.join(args.crops)}")

    if not args.data_dirs:
        print("\nERROR: no --data-dirs given. Nothing to train on.")
        print("Example:")
        print("  python train_kaggle.py --data-dirs /kaggle/input/plantvillage-dataset/"
              "plantvillage\\ dataset/color")
        return 2

    # ---- discovery -------------------------------------------------------
    print("\n[1/6] Discovering datasets")
    raw, notes = collect_from_roots(args.data_dirs)
    for note in notes:
        print(f"  - {note}")

    index, unmapped, warnings = build_class_index(
        raw,
        all_classes,
        crops=args.crops,
        strict=not args.allow_missing_classes,
    )
    for warning in warnings:
        print(f"  ! {warning}")

    present = [label for label in all_classes if label in index]
    total_images = sum(len(paths) for paths in index.values())
    if not present:
        print("\nERROR: no supported classes found in the given dataset roots.")
        return 2

    print(f"\n  mapped {total_images} images into {len(present)} supported classes")
    by_crop: Dict[str, int] = defaultdict(int)
    for label in present:
        by_crop[crop_of(label)] += len(index[label])
    for crop, count in sorted(by_crop.items()):
        print(f"    {crop:<8} {count:>6} images")

    print("\n  class coverage")
    print(f"    {'class':<48}{'images':>8}")
    for label in present:
        print(f"    {label:<48}{len(index[label]):>8}")
    missing = [label for label in all_classes if label not in index and crop_of(label) in set(args.crops)]
    if missing:
        print(f"    (not trained this run: {len(missing)} supported classes had no images)")

    if args.inspect_only:
        print("\n--inspect-only given: stopping before training.")
        return 0

    # ---- split -----------------------------------------------------------
    print("\n[2/6] Splitting train/validation")
    train_samples, val_samples = stratified_split(index, present, args.val_split, args.seed)
    if not train_samples:
        print("ERROR: training split is empty.")
        return 2
    if not val_samples:
        print("ERROR: validation split is empty — add more images per class.")
        return 2
    print(f"  train: {len(train_samples)} images")
    print(f"  val  : {len(val_samples)} images")

    train_ds = LeafDataset(train_samples, train_transform(image_size, mean, std))
    val_ds = LeafDataset(val_samples, eval_transform(image_size, mean, std))

    loader_kwargs = dict(
        batch_size=args.batch_size,
        num_workers=args.num_workers,
        pin_memory=(device.type == "cuda"),
    )
    if args.num_workers > 0:
        loader_kwargs["persistent_workers"] = True

    train_loader = DataLoader(train_ds, shuffle=True, drop_last=False, **loader_kwargs)
    val_loader = DataLoader(val_ds, shuffle=False, **loader_kwargs)

    # ---- model -----------------------------------------------------------
    print("\n[3/6] Building MobileNetV3-Large")
    model = build_model(
        num_classes=len(present),
        pretrained=not args.no_pretrained,
        dropout=args.dropout,
        allow_download=not args.offline,
    ).to(device)
    backbone_pretrained = bool(getattr(model, "cropguard_pretrained", False))
    print(f"  trainable parameters: {count_parameters(model):,}")
    print(f"  classifier head     : {model.classifier[-1]}")

    class_weights = None
    if args.class_weights:
        counts = Counter(label_id for _, label_id in train_samples)
        weights = torch.tensor(
            [len(train_samples) / (len(present) * max(counts[i], 1)) for i in range(len(present))],
            dtype=torch.float32,
            device=device,
        )
        class_weights = weights
        print("  class-weighted loss : enabled")

    criterion = nn.CrossEntropyLoss(weight=class_weights, label_smoothing=args.label_smoothing)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=args.weight_decay)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=max(args.epochs, 1))
    use_amp = device.type == "cuda"
    scaler = torch.amp.GradScaler("cuda", enabled=use_amp)

    # ---- train -----------------------------------------------------------
    os.makedirs(args.out_dir, exist_ok=True)
    log_path = os.path.join(args.out_dir, "training_log.csv")
    metrics_path = os.path.join(args.out_dir, "training_metrics.json")
    best_path = os.path.join(args.out_dir, CHECKPOINT_FILENAME)

    best_accuracy = -1.0
    best_epoch = -1
    best_metrics: dict = {}
    epochs_without_improvement = 0
    history: List[dict] = []
    started = time.time()

    print(f"\n[4/6] Training for up to {args.epochs} epochs (AMP: {'on' if use_amp else 'off'})")
    for epoch in range(1, args.epochs + 1):
        model.train()
        epoch_started = time.time()
        running_loss = 0.0
        seen = 0
        correct = 0

        for images, labels in train_loader:
            images = images.to(device, non_blocking=True)
            labels = labels.to(device, non_blocking=True)

            optimizer.zero_grad(set_to_none=True)
            with torch.amp.autocast("cuda", enabled=use_amp):
                logits = model(images)
                loss = criterion(logits, labels)

            scaler.scale(loss).backward()
            scaler.step(optimizer)
            scaler.update()

            running_loss += float(loss.item()) * labels.size(0)
            seen += labels.size(0)
            correct += int((logits.argmax(dim=1) == labels).sum().item())

        scheduler.step()

        train_loss = running_loss / max(seen, 1)
        train_accuracy = correct / max(seen, 1)
        val_metrics = evaluate(model, val_loader, device, present, criterion)
        elapsed = time.time() - epoch_started

        row = {
            "epoch": epoch,
            "train_loss": round(train_loss, 4),
            "train_accuracy": round(train_accuracy, 4),
            "val_loss": val_metrics["val_loss"],
            "val_accuracy": val_metrics["val_accuracy"],
            "macro_recall": val_metrics["macro_recall"],
            "lr": round(optimizer.param_groups[0]["lr"], 8),
            "seconds": round(elapsed, 1),
        }
        history.append(row)

        print(
            f"  epoch {epoch:>2}/{args.epochs}  "
            f"train_loss {train_loss:.4f}  train_acc {train_accuracy:.4f}  "
            f"val_loss {val_metrics['val_loss']:.4f}  val_acc {val_metrics['val_accuracy']:.4f}  "
            f"macro_recall {val_metrics['macro_recall']:.4f}  ({elapsed:.0f}s)"
        )

        if val_metrics["val_accuracy"] is not None and val_metrics["val_accuracy"] > best_accuracy:
            best_accuracy = val_metrics["val_accuracy"]
            best_epoch = epoch
            best_metrics = val_metrics
            epochs_without_improvement = 0
            save_checkpoint(
                best_path,
                model,
                present,
                spec,
                artifact_type=ARTIFACT_TRAINED,
                extra={
                    "val_accuracy": best_metrics["val_accuracy"],
                    "macro_recall": best_metrics["macro_recall"],
                    "epoch": best_epoch,
                    "train_samples": len(train_samples),
                    "val_samples": len(val_samples),
                    "crops": sorted({crop_of(label) for label in present}),
                    "backbone_pretrained": backbone_pretrained,
                    "data_dirs": list(args.data_dirs),
                    "per_class_accuracy": best_metrics["per_class_accuracy"],
                },
            )
            print(f"    -> new best (val_acc {best_accuracy:.4f}) saved to {best_path}")
        else:
            epochs_without_improvement += 1
            if args.early_stop_patience > 0 and epochs_without_improvement >= args.early_stop_patience:
                print(f"  early stopping: no improvement for {epochs_without_improvement} epochs")
                break

    unreadable_train = sorted(set(train_ds.skipped))
    unreadable_val = sorted(set(val_ds.skipped))
    if unreadable_train:
        print(f"  ! {len(unreadable_train)} unreadable training image(s) were replaced with blank frames")
    if unreadable_val:
        print(f"  ! {len(unreadable_val)} unreadable validation image(s) were replaced with blank frames")

    if best_epoch < 0:
        print("\nERROR: training produced no valid checkpoint.")
        return 2

    # ---- export ----------------------------------------------------------
    print("\n[5/6] Exporting artifacts")
    class_names_path = save_class_names(os.path.join(args.out_dir, CLASS_NAMES_FILENAME), present)
    print(f"  model       : {best_path}")
    print(f"  class names : {class_names_path}")

    with open(log_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(history[0].keys()))
        writer.writeheader()
        writer.writerows(history)
    print(f"  log         : {log_path}")

    report = {
        "model_name": "mobilenet_v3_large",
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "device": str(device),
        "epochs_requested": args.epochs,
        "epochs_run": len(history),
        "best_epoch": best_epoch,
        "best_val_accuracy": best_metrics.get("val_accuracy"),
        "best_macro_recall": best_metrics.get("macro_recall"),
        "train_samples": len(train_samples),
        "val_samples": len(val_samples),
        "image_size": image_size,
        "backbone_pretrained": backbone_pretrained,
        "batch_size": args.batch_size,
        "learning_rate": args.lr,
        "class_names": present,
        "classes_not_trained": missing,
        "per_class_accuracy": best_metrics.get("per_class_accuracy"),
        "top_confusions": best_metrics.get("top_confusions"),
        "history": history,
        "data_dirs": list(args.data_dirs),
        "unreadable_files": {"train": unreadable_train, "val": unreadable_val},
        "duration_seconds": round(time.time() - started, 1),
    }
    with open(metrics_path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)
        handle.write("\n")
    print(f"  metrics     : {metrics_path}")

    # ---- summary ---------------------------------------------------------
    print("\n[6/6] Summary")
    print(f"  best epoch          : {best_epoch}")
    print(f"  best val accuracy   : {best_accuracy:.4f}")
    print(f"  best macro recall   : {best_metrics.get('macro_recall')}")
    print(f"  total time          : {report['duration_seconds']:.0f}s")
    print("\n  per-class validation accuracy")
    for label, accuracy in (best_metrics.get("per_class_accuracy") or {}).items():
        print(f"    {label:<48}{accuracy:>7.4f}")
    if best_metrics.get("top_confusions"):
        print("\n  most frequent confusions")
        for item in best_metrics["top_confusions"]:
            print(f"    {item['true']} -> {item['predicted_as']}  ({item['count']}x)")

    print("\n  The model can ONLY predict the classes below. Anything else is out of scope:")
    for label in present:
        print(f"    - {label}")
    print("\n  Copy the exported artifacts into the backend:")
    print(f"    cp {best_path} {class_names_path} ../backend/model/")
    print("\n  Verify the export end-to-end with a real leaf photo:")
    print(f"    python train_kaggle.py --predict /path/to/leaf.jpg --out-dir {args.out_dir}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
