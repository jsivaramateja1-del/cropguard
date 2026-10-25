#!/usr/bin/env python3
"""Export the trained PyTorch checkpoint to ONNX.

Why this exists: the PyTorch inference stack peaks at ~660 MB resident, which cannot
fit a 512 MB free host. The ONNX Runtime stack peaks at ~151 MB for the same graph and
the same numbers, which is what makes a free deployment viable.

The export verifies itself. It runs both backends on the same random input and fails
loudly if the logits differ, so a silent export bug cannot reach production.

Usage:
    python export_onnx.py --checkpoint ../backend/model/cropguard_mobilenetv3.pth
    python export_onnx.py --checkpoint /kaggle/working/export/cropguard_mobilenetv3.pth \
        --out-dir ../backend/model

Writes, next to the output:
    cropguard_mobilenetv3.onnx     the deployable graph
    cropguard_mobilenetv3.artifact artifact type marker, read by backend/inference.py
    class_names.json               label order (already tracked in the repo)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Sequence

import numpy as np
import torch

from cropguard_common import CHECKPOINT_FILENAME, CLASS_NAMES_FILENAME, build_model, load_checkpoint

ONNX_FILENAME = "cropguard_mobilenetv3.onnx"
ARTIFACT_MARKER = "cropguard_mobilenetv3.artifact"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    here = os.path.dirname(os.path.abspath(__file__))
    default_model_dir = os.path.normpath(os.path.join(here, "..", "backend", "model"))
    parser = argparse.ArgumentParser(
        description="Export a CropGuard checkpoint to ONNX and verify equivalence.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--checkpoint",
        default=os.path.join(default_model_dir, CHECKPOINT_FILENAME),
        help="PyTorch checkpoint produced by train_kaggle.py",
    )
    parser.add_argument("--out-dir", default=default_model_dir)
    parser.add_argument("--opset", type=int, default=17)
    parser.add_argument(
        "--tolerance",
        type=float,
        default=1e-4,
        help="Maximum allowed absolute difference between torch and ONNX logits",
    )
    parser.add_argument(
        "--skip-verify",
        action="store_true",
        help="Export without the equivalence check (not recommended)",
    )
    return parser.parse_args(argv)


def _export_onnx(model, dummy, path: str, opset: int) -> str:
    """Export, coping with torch's two ONNX exporter generations.

    torch < 2.6 has only the legacy TorchScript exporter. torch >= 2.9 defaults to the
    dynamo exporter, which requires the separate ``onnxscript`` package. Rather than
    making the caller know which torch they have, try the exporter that needs no extra
    dependency first and fall back to the other.

    Returns a short description of the exporter that succeeded.
    """
    import inspect

    common = dict(
        input_names=["input"],
        output_names=["logits"],
        dynamic_axes={"input": {0: "batch"}, "logits": {0: "batch"}},
        opset_version=opset,
        do_constant_folding=True,
    )

    supports_dynamo = "dynamo" in inspect.signature(torch.onnx.export).parameters
    attempts = []
    if supports_dynamo:
        attempts.append(({"dynamo": False}, "legacy TorchScript (dynamo=False)"))
        attempts.append(({"dynamo": True}, "dynamo (needs onnxscript)"))
    else:
        attempts.append(({}, f"legacy TorchScript (torch {torch.__version__})"))

    missing_dependency = None
    failures = []
    for extra, description in attempts:
        try:
            torch.onnx.export(model, dummy, path, **common, **extra)
            return description
        except Exception as error:  # noqa: BLE001 - try the other exporter
            missing_dependency = error
            failures.append(f"{description}: {type(error).__name__}: {error}")
            print(f"  ! {description} unavailable: {type(error).__name__}: {error}")
            continue

    raise SystemExit(
        "ONNX export failed with every exporter this torch build offers.\n"
        "The most common cause on torch 2.9+ is a missing 'onnxscript' package.\n"
        "Install it and re-run:\n"
        "    pip install onnxscript\n\n"
        "Attempts:\n  " + "\n  ".join(failures)
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)

    if not os.path.isfile(args.checkpoint):
        print(f"ERROR: checkpoint not found: {args.checkpoint}")
        print("Train one with train_kaggle.py, or point --checkpoint at your export.")
        return 2

    payload = load_checkpoint(args.checkpoint)
    class_names = list(payload["class_names"])
    image_size = int(payload["image_size"])
    artifact_type = str(payload.get("artifact_type", "unknown"))

    print("=" * 70)
    print("CropGuard ONNX export")
    print("=" * 70)
    print(f"checkpoint   : {args.checkpoint}")
    print(f"artifact     : {artifact_type}")
    print(f"classes      : {len(class_names)}")
    print(f"image size   : {image_size}")

    model = build_model(len(class_names), pretrained=False)
    model.load_state_dict(payload["state_dict"])
    model.eval()

    os.makedirs(args.out_dir, exist_ok=True)
    onnx_path = os.path.join(args.out_dir, ONNX_FILENAME)

    dummy = torch.randn(1, 3, image_size, image_size)
    exporter = _export_onnx(model, dummy, onnx_path, args.opset)
    print(f"exporter     : {exporter}")
    size_mb = os.path.getsize(onnx_path) / (1024 * 1024)
    print(f"\nwrote        : {onnx_path} ({size_mb:.1f} MB)")

    # The backend reads this marker to know whether it is serving a real model.
    marker_path = os.path.join(args.out_dir, ARTIFACT_MARKER)
    with open(marker_path, "w", encoding="utf-8") as handle:
        handle.write(artifact_type + "\n")
    print(f"wrote        : {marker_path} ({artifact_type})")

    names_path = os.path.join(args.out_dir, CLASS_NAMES_FILENAME)
    with open(names_path, "w", encoding="utf-8") as handle:
        json.dump(class_names, handle, indent=2)
        handle.write("\n")
    print(f"wrote        : {names_path}")

    if args.skip_verify:
        print("\n--skip-verify given: equivalence not checked.")
        return 0

    print("\nverifying equivalence with the PyTorch graph")
    try:
        import onnxruntime as ort
    except ImportError:
        print("  ! onnxruntime is not installed, so equivalence cannot be checked.")
        print("    Install it with: pip install onnxruntime")
        return 1

    session = ort.InferenceSession(onnx_path, providers=["CPUExecutionProvider"])
    input_name = session.get_inputs()[0].name

    worst = 0.0
    argmax_matches = True
    for trial in range(3):
        sample = np.random.rand(1, 3, image_size, image_size).astype(np.float32)
        with torch.no_grad():
            torch_logits = model(torch.from_numpy(sample)).numpy()
        onnx_logits = session.run(None, {input_name: sample})[0]

        difference = float(np.max(np.abs(torch_logits - onnx_logits)))
        worst = max(worst, difference)
        if int(np.argmax(torch_logits)) != int(np.argmax(onnx_logits)):
            argmax_matches = False
        print(f"  trial {trial + 1}: max abs diff {difference:.3e}")

    print(f"\nworst difference : {worst:.3e} (tolerance {args.tolerance:.0e})")
    print(f"argmax agreement : {'yes' if argmax_matches else 'NO'}")

    if worst > args.tolerance or not argmax_matches:
        print("\nFAILED: the ONNX graph does not reproduce the PyTorch output.")
        return 1

    print("\nOK — the ONNX graph is numerically equivalent to the checkpoint.")
    print("\nUpload BOTH files to the repository, or the backend will refuse to load them:")
    print(f"  {onnx_path}")
    print(f"  {names_path}")
    print("The class_names.json must be the one written here — it matches this graph's")
    print("label order. Uploading the .onnx alone leaves a mismatched label list behind.")
    print("On github.com: backend/model/ -> Add file -> Upload files -> commit.")
    print("Peak memory with onnxruntime is ~151 MB, versus ~660 MB with torch.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
