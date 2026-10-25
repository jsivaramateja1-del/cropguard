#!/usr/bin/env python3
"""CropGuard — development model artifact generator.

WHAT THIS DOES
--------------
Creates a structurally valid MobileNetV3-Large checkpoint with the exact
architecture, label list and preprocessing contract that the real Kaggle-trained
model will have. It exists so the backend inference pipeline (Phase 3) can be
built, loaded and tested before a GPU training run finishes.

WHAT THIS DOES *NOT* DO
-----------------------
It does not produce a disease classifier. The classifier head is randomly
initialised, so its output is meaningless: every score is close to uniform
(1/21 for 21 classes) and no class is genuinely favoured. That is deliberate —
a random head yields low confidence, which means every report made against this
artifact correctly lands in Pending_Expert_Review instead of being trusted.

The checkpoint is tagged ``artifact_type: "development_untrained"``. The backend
reads that tag and reports the model as unavailable-for-diagnosis rather than
presenting random scores as a prediction.

Replace it with the real export from ml_training/train_kaggle.py before any
demo, and never ship this file as a trained model.

Usage
-----
    python generate_starter_model.py                    # writes into ../backend/model
    python generate_starter_model.py --verify           # build, then reload and self-test
    python generate_starter_model.py --pretrained       # ImageNet backbone, random head
    python generate_starter_model.py --out-dir /tmp/x --seed 7
"""

from __future__ import annotations

import argparse
import os
import random
import sys
from typing import Sequence

import torch
from PIL import Image

from cropguard_common import (
    ARTIFACT_DEVELOPMENT,
    CHECKPOINT_FILENAME,
    CLASS_NAMES_FILENAME,
    build_model,
    count_parameters,
    eval_transform,
    load_canonical,
    load_checkpoint,
    save_checkpoint,
    save_class_names,
)

DEVELOPMENT_WARNING = (
    "DEVELOPMENT ARTIFACT — randomly initialised classifier head. "
    "Outputs are not disease predictions. Train and export the real model with "
    "ml_training/train_kaggle.py before any demo or deployment."
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    here = os.path.dirname(os.path.abspath(__file__))
    default_out = os.path.normpath(os.path.join(here, "..", "backend", "model"))

    parser = argparse.ArgumentParser(
        description="Generate a structural placeholder checkpoint for backend development.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--out-dir", default=default_out, help="Where to write the artifacts.")
    parser.add_argument("--seed", type=int, default=1337, help="Seed for reproducible head init.")
    parser.add_argument(
        "--pretrained",
        action="store_true",
        help="Load ImageNet backbone weights (needs network). The head stays random either way.",
    )
    parser.add_argument("--dropout", type=float, default=0.2)
    parser.add_argument(
        "--verify",
        action="store_true",
        help="After writing, reload the checkpoint and run a self-test forward pass.",
    )
    parser.add_argument("--force", action="store_true", help="Overwrite an existing checkpoint.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)

    spec = load_canonical()
    class_names = spec["classes"]
    image_size = int(spec["image_size"])
    mean = spec["normalization"]["mean"]
    std = spec["normalization"]["std"]

    checkpoint_path = os.path.join(args.out_dir, CHECKPOINT_FILENAME)
    class_names_path = os.path.join(args.out_dir, CLASS_NAMES_FILENAME)

    print("=" * 74)
    print("CropGuard development artifact generator")
    print("=" * 74)
    print(f"torch        : {torch.__version__}")
    print(f"architecture : mobilenet_v3_large")
    print(f"classes      : {len(class_names)}")
    print(f"image size   : {image_size}")

    if os.path.exists(checkpoint_path) and not args.force:
        existing = load_checkpoint(checkpoint_path)
        kind = existing.get("artifact_type", "unknown")
        if kind == "trained":
            print(f"\nRefusing to overwrite {checkpoint_path}: it is a TRAINED model.")
            print("Re-run with --force only if you are certain you want to discard it.")
            return 1
        print(f"\nExisting development artifact found ({kind}); overwriting.")

    random.seed(args.seed)
    torch.manual_seed(args.seed)

    model = build_model(len(class_names), pretrained=args.pretrained, dropout=args.dropout)
    model.eval()
    print(f"parameters   : {count_parameters(model):,}")
    print(f"backbone     : {'ImageNet-pretrained' if args.pretrained else 'random init'}")
    print("head         : random init (intentional)")

    save_checkpoint(
        checkpoint_path,
        model,
        class_names,
        spec,
        artifact_type=ARTIFACT_DEVELOPMENT,
        extra={
            "warning": DEVELOPMENT_WARNING,
            "val_accuracy": None,
            "seed": args.seed,
            "pretrained_backbone": bool(args.pretrained),
            "purpose": "Backend pipeline development only. Not a disease classifier.",
        },
    )
    save_class_names(class_names_path, class_names)

    size_mb = os.path.getsize(checkpoint_path) / (1024 * 1024)
    print(f"\nwrote model       : {checkpoint_path} ({size_mb:.1f} MB)")
    print(f"wrote class names : {class_names_path}")

    if args.verify:
        print("\n[verify] reloading checkpoint")
        payload = load_checkpoint(checkpoint_path)
        assert payload["artifact_type"] == ARTIFACT_DEVELOPMENT, "artifact_type mismatch"
        assert payload["class_names"] == class_names, "class list mismatch"
        assert int(payload["num_classes"]) == len(class_names), "num_classes mismatch"

        from cropguard_common import checkpoint_to_model  # local import keeps CLI startup light

        reloaded = checkpoint_to_model(payload)
        transform = eval_transform(image_size, mean, std)
        dummy_image = Image.new("RGB", (image_size, image_size), (118, 148, 92))
        dummy = transform(dummy_image).unsqueeze(0)
        with torch.no_grad():
            probabilities = torch.softmax(reloaded(dummy), dim=1)[0]

        top_probability, top_index = probabilities.max(dim=0)
        print(f"[verify] num_classes      : {payload['num_classes']}")
        print(f"[verify] top-1 class      : {class_names[int(top_index)]}")
        print(f"[verify] top-1 probability: {float(top_probability):.4f}")
        print(f"[verify] max probability  : {float(probabilities.max()):.4f} (uniform ≈ {1 / len(class_names):.4f})")

        if float(top_probability) >= 0.75:
            print("[verify] WARNING: random head produced a high confidence score; "
                  "review the initialisation before relying on the routing behaviour.")
        else:
            print("[verify] as expected: confidence is far below the 0.75 expert-review "
                  "threshold, so reports route to Pending_Expert_Review.")
        print("[verify] OK — checkpoint loads and runs inference.")

    print("\n" + "-" * 74)
    print("REMINDER: this file cannot diagnose anything. It is a wiring test only.")
    print("Train the real model on Kaggle, then export over this file:")
    print("  cd ml_training && python train_kaggle.py --data-dirs <datasets> --epochs 12")
    print("-" * 74)
    return 0


if __name__ == "__main__":
    sys.exit(main())
