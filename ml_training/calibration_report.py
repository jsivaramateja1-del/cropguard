#!/usr/bin/env python3
"""CropGuard — confidence calibration report.

Answers one product question: does the 0.75 confidence gate actually separate good
predictions from bad ones?

CropGuard routes a report to expert review when confidence is below 0.75. That gate
is only meaningful if low-confidence predictions are genuinely less accurate than
high-confidence ones. This script measures that on the validation split, so the
threshold can be chosen from evidence rather than habit.

It reports:
  * overall validation accuracy
  * the confidence distribution (mean, median, percentiles)
  * how many predictions fall below 0.75 / 0.85 / 0.90
  * accuracy among low-confidence vs high-confidence predictions  <- the real test
  * a reliability table (confidence bin vs observed accuracy)
  * per-class mean confidence

A large gap between low-confidence and high-confidence accuracy means the gate works.
No gap means confidence carries no information and the threshold is decorative.

Usage (Kaggle, same data as training):
    python calibration_report.py \
        --checkpoint /kaggle/working/export/cropguard_mobilenetv3.pth \
        --data-dirs "/kaggle/input/datasets/abdallahalidev/plantvillage-dataset/color" \
                    "/kaggle/input/datasets/vbookshelf/rice-leaf-diseases/rice_leaf_diseases:Rice" \
        --crops Tomato Potato Rice

The split is rebuilt with the same seed and ratio as training, so the numbers are
directly comparable to the training log.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter, defaultdict
from typing import Dict, List, Sequence, Tuple

import numpy as np
import torch
from torch.utils.data import DataLoader

from cropguard_common import (
    build_class_index,
    checkpoint_to_model,
    collect_from_roots,
    crop_of,
    eval_transform,
    load_canonical,
    load_checkpoint,
)
from train_kaggle import LeafDataset, stratified_split

THRESHOLDS = (0.75, 0.85, 0.90)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    here = os.path.dirname(os.path.abspath(__file__))
    default_ckpt = os.path.normpath(os.path.join(here, "..", "backend", "model", "cropguard_mobilenetv3.pth"))
    parser = argparse.ArgumentParser(
        description="Measure whether the confidence gate separates correct from incorrect predictions.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--checkpoint", default=default_ckpt)
    parser.add_argument("--data-dirs", nargs="+", required=True)
    parser.add_argument("--crops", nargs="+", default=["Tomato", "Rice", "Potato", "Wheat"])
    parser.add_argument("--val-split", type=float, default=0.2)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--num-workers", type=int, default=2)
    parser.add_argument(
        "--allow-missing-classes",
        action="store_true",
        default=True,
        help="Always on here: this script inspects an existing checkpoint rather than training.",
    )
    return parser.parse_args(argv)


@torch.no_grad()
def collect_predictions(
    checkpoint_path: str, args: argparse.Namespace
) -> Tuple[np.ndarray, np.ndarray, List[str], dict]:
    payload = load_checkpoint(checkpoint_path)
    class_names: List[str] = list(payload["class_names"])
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = checkpoint_to_model(payload, device=str(device))

    spec = load_canonical()
    raw, _notes = collect_from_roots(args.data_dirs)
    index, _unmapped, _warnings = build_class_index(
        raw, class_names, crops=args.crops, strict=False
    )

    # The split must match training exactly: same classes, ratio and seed.
    present = [label for label in class_names if label in index]
    _train, val = stratified_split(index, present, args.val_split, args.seed)
    if not val:
        raise SystemExit("Validation split is empty — check --data-dirs and --val-split.")

    dataset = LeafDataset(
        val,
        eval_transform(
            int(payload["image_size"]),
            payload["normalization"]["mean"],
            payload["normalization"]["std"],
        ),
    )
    loader = DataLoader(
        dataset,
        batch_size=args.batch_size,
        shuffle=False,
        num_workers=args.num_workers,
        pin_memory=(device.type == "cuda"),
    )

    probabilities: List[np.ndarray] = []
    truths: List[int] = []
    for images, labels in loader:
        logits = model(images.to(device))
        probabilities.append(torch.softmax(logits, dim=1).cpu().numpy())
        truths.extend(labels.tolist())

    return np.vstack(probabilities), np.array(truths), class_names, payload


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    probs, truths, class_names, payload = collect_predictions(args.checkpoint, args)
    predictions = probs.argmax(axis=1)
    confidences = probs.max(axis=1)
    correct = predictions == truths

    print("=" * 74)
    print("CropGuard confidence calibration report")
    print("=" * 74)
    print(f"checkpoint        : {args.checkpoint}")
    print(f"artifact type     : {payload.get('artifact_type', 'unknown')}")
    print(f"classes           : {len(class_names)}")
    print(f"validation images : {len(truths)}")

    if payload.get("artifact_type") != "trained":
        print("\nWARNING: this is a development artifact. The numbers below describe")
        print("         random weights and say nothing about real behaviour.")

    accuracy = float(correct.mean())
    print(f"\naccuracy          : {accuracy:.4f}")
    print(f"mean confidence   : {float(confidences.mean()):.4f}")
    print(f"median confidence : {float(np.median(confidences)):.4f}")
    for percentile in (5, 25, 50, 75, 95):
        print(f"  p{percentile:<2}            : {float(np.percentile(confidences, percentile)):.4f}")

    print("\n-- how many reports would go to expert review --")
    print(f"{'threshold':>10}{'below':>10}{'share':>9}{'acc(below)':>12}{'acc(above)':>12}{'gap':>8}")
    for threshold in THRESHOLDS:
        below = confidences < threshold
        n_below = int(below.sum())
        share = n_below / len(confidences)
        acc_below = float(correct[below].mean()) if n_below else float("nan")
        acc_above = float(correct[~below].mean()) if (~below).any() else float("nan")
        gap = acc_above - acc_below if n_below and (~below).any() else float("nan")
        print(
            f"{threshold:>10.2f}{n_below:>10}{share:>9.1%}"
            f"{acc_below:>12.4f}{acc_above:>12.4f}{gap:>8.4f}"
        )

    print("\n-- reliability: confidence bin vs observed accuracy --")
    bins = [(0.0, 0.5), (0.5, 0.6), (0.6, 0.7), (0.7, 0.75), (0.75, 0.85),
            (0.85, 0.95), (0.95, 1.0001)]
    print(f"{'bin':>14}{'count':>8}{'share':>9}{'accuracy':>10}")
    for low, high in bins:
        mask = (confidences >= low) & (confidences < high)
        count = int(mask.sum())
        if count == 0:
            print(f"{f'{low:.2f}-{high:.2f}':>14}{count:>8}{0:>9.1%}{'-':>10}")
            continue
        print(
            f"{f'{low:.2f}-{high:.2f}':>14}{count:>8}{count/len(confidences):>9.1%}"
            f"{float(correct[mask].mean()):>10.4f}"
        )

    print("\n-- per-class mean confidence --")
    per_class_conf: Dict[str, List[float]] = defaultdict(list)
    per_class_correct: Counter = Counter()
    per_class_total: Counter = Counter()
    for truth, confidence, ok in zip(truths, confidences, correct):
        label = class_names[int(truth)]
        per_class_conf[label].append(float(confidence))
        per_class_total[label] += 1
        per_class_correct[label] += int(ok)
    print(f"{'class':<48}{'n':>5}{'acc':>8}{'conf':>8}")
    for label in class_names:
        if not per_class_total[label]:
            continue
        print(
            f"{label:<48}{per_class_total[label]:>5}"
            f"{per_class_correct[label]/per_class_total[label]:>8.2f}"
            f"{np.mean(per_class_conf[label]):>8.3f}"
        )

    print("\n-- crop summary --")
    by_crop_conf: Dict[str, List[float]] = defaultdict(list)
    by_crop_correct: Counter = Counter()
    by_crop_total: Counter = Counter()
    for truth, confidence, ok in zip(truths, confidences, correct):
        crop = crop_of(class_names[int(truth)])
        by_crop_conf[crop].append(float(confidence))
        by_crop_total[crop] += 1
        by_crop_correct[crop] += int(ok)
    for crop in sorted(by_crop_total):
        print(
            f"  {crop:<8} n={by_crop_total[crop]:<5} "
            f"acc={by_crop_correct[crop]/by_crop_total[crop]:.4f} "
            f"mean_conf={np.mean(by_crop_conf[crop]):.4f}"
        )

    print("\n-- verdict --")
    below_75 = confidences < 0.75
    above_75 = ~below_75
    if below_75.sum() == 0:
        print("  Every validation prediction is at or above 0.75, so the expert-review")
        print("  gate would never fire on this data. Either the model is extremely")
        print("  confident, or the split is too easy (near-duplicate images across")
        print("  train/val). Field photos are the real test.")
    elif above_75.sum() == 0:
        print(f"  Every prediction ({int(below_75.sum())}) falls below 0.75, so nothing would be")
        print("  auto-verified and no comparison between high and low confidence is possible.")
        print("  A checkpoint that does not match its data looks exactly like this — check that")
        print("  the checkpoint and --data-dirs belong together, and that the artifact is not")
        print("  the randomly initialised development model.")
    else:
        gap = float(correct[above_75].mean() - correct[below_75].mean())
        print(f"  {below_75.mean():.1%} of predictions fall below 0.75 and would route to expert review.")
        print(f"  Accuracy gap between high- and low-confidence predictions: {gap:+.4f}")
        if gap > 0.05:
            print("  The gate is informative: low-confidence predictions really are worse.")
        elif gap > 0:
            print("  The gate is weakly informative. Confidence separates errors only slightly.")
        else:
            print("  The gate is NOT informative on this data: low-confidence predictions are")
            print("  no worse than high-confidence ones, so 0.75 is an arbitrary cut.")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
