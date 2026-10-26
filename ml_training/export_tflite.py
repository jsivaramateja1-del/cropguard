#!/usr/bin/env python3
"""Export the trained checkpoint to TensorFlow Lite for on-device inference.

Why this exists: the pitch promises the model runs offline on a low-cost Android phone.
The backend serves ONNX; a phone needs TFLite. This converts the same trained weights, so
both paths classify identically.

Two routes, tried in order:

1. **ai-edge-torch** (Google's PyTorch → TFLite exporter). Cleanest, and it handles
   MobileNetV3's operators well.
2. **ONNX → TensorFlow → TFLite** via ``onnx2tf``. Used when route 1 is unavailable.

Both are optional, heavy dependencies, so they are imported lazily and the script says
plainly which route it used — or that neither is installed.

Usage on Kaggle (GPU not needed):

    !pip install ai-edge-torch ai-edge-litert
    !python ml_training/export_tflite.py \
        --checkpoint /kaggle/working/export/cropguard_mobilenetv3.pth \
        --out-dir /kaggle/working/export

Then copy `cropguard_mobilenetv3.tflite` and `class_names.json` into the Android app's
assets. `--verify` checks the TFLite output against PyTorch on random inputs before you
trust it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cropguard_common import load_canonical  # noqa: E402


def load_checkpoint(path: Path):
    """Load the trained model, tolerating the metadata we write alongside it."""
    from cropguard_common import build_model  # imported late: needs torch

    checkpoint = torch.load(path, map_location="cpu", weights_only=False)
    class_names = checkpoint.get("class_names")
    if not class_names:
        raise SystemExit(
            "The checkpoint has no class_names. Re-export it with train_kaggle.py, or pass "
            "--class-names pointing at class_names.json."
        )

    model = build_model(len(class_names), pretrained=False)
    state = checkpoint.get("model_state") or checkpoint.get("state_dict") or checkpoint
    model.load_state_dict(state)
    model.eval()
    return model, list(class_names)


def export_via_ai_edge_torch(model, out_path: Path, image_size: int) -> str:
    import ai_edge_torch  # noqa: PLC0415

    sample = torch.randn(1, 3, image_size, image_size)
    edge_model = ai_edge_torch.convert(model.eval(), (sample,))
    edge_model.export(str(out_path))
    return "ai-edge-torch"


def export_via_onnx(model, out_path: Path, image_size: int, tmp_dir: Path) -> str:
    import onnx
    import onnx2tf  # noqa: PLC0415

    onnx_path = tmp_dir / "model.onnx"
    torch.onnx.export(
        model.eval(),
        torch.randn(1, 3, image_size, image_size),
        str(onnx_path),
        input_names=["input"],
        output_names=["logits"],
        opset_version=18,
        dynamo=False,
    )
    onnx2tf.convert(
        input_onnx_file_path=str(onnx_path),
        output_folder_path=str(tmp_dir / "tf"),
        non_verbose=True,
    )
    candidates = list((tmp_dir / "tf").glob("*_float32.tflite")) + list(
        (tmp_dir / "tf").glob("*.tflite")
    )
    if not candidates:
        raise SystemExit("onnx2tf produced no .tflite file")
    candidates[0].replace(out_path)
    return "onnx2tf"


def verify(model, tflite_path: Path, image_size: int) -> None:
    """Compare TFLite logits against PyTorch on random inputs."""
    import numpy as np

    try:
        from ai_edge_litert.interpreter import Interpreter  # noqa: PLC0415
    except ImportError:
        try:
            from tflite_runtime.interpreter import Interpreter  # noqa: PLC0415
        except ImportError:
            from tensorflow.lite.python.interpreter import Interpreter  # noqa: PLC0415

    interpreter = Interpreter(model_path=str(tflite_path))
    interpreter.allocate_tensors()
    input_detail = interpreter.get_input_details()[0]
    output_detail = interpreter.get_output_details()[0]

    sample = torch.randn(1, 3, image_size, image_size)
    with torch.no_grad():
        reference = model(sample).numpy()

    # TFLite wants NHWC for image models.
    interpreter.set_tensor(
        input_detail["index"], sample.permute(0, 2, 3, 1).numpy().astype(np.float32)
    )
    interpreter.invoke()
    produced = interpreter.get_tensor(output_detail["index"])

    if produced.shape != reference.shape:
        print(f"  shape mismatch: tflite {produced.shape} vs torch {reference.shape}")
        print("  (a transpose may be needed at the call site; check the app's preprocessing)")
        return

    difference = float(np.max(np.abs(produced - reference)))
    print(f"  max abs difference vs PyTorch: {difference:.3e}")
    if difference > 1e-2:
        print("  WARNING: outputs diverge. Do not ship this artifact before investigating.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--checkpoint", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--image-size", type=int, default=224)
    parser.add_argument("--class-names", type=Path, default=None)
    parser.add_argument("--verify", action="store_true", help="compare TFLite against PyTorch")
    parser.add_argument("--quantize", choices=["none", "float16", "int8"], default="float16")
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    model, class_names = load_checkpoint(args.checkpoint)

    if args.class_names:
        class_names = json.loads(args.class_names.read_text())
    canonical = load_canonical()
    unknown = [name for name in class_names if name not in canonical["labels"]]
    if unknown:
        raise SystemExit(f"checkpoint labels are not in the canonical list: {unknown}")

    out_path = args.out_dir / "cropguard_mobilenetv3.tflite"
    route = None
    for attempt in ("ai-edge-torch", "onnx2tf"):
        try:
            if attempt == "ai-edge-torch":
                route = export_via_ai_edge_torch(model, out_path, args.image_size)
            else:
                route = export_via_onnx(model, out_path, args.image_size, args.out_dir / "_tmp")
            break
        except ImportError:
            print(f"  {attempt} is not installed, trying the next route")
        except Exception as error:  # noqa: BLE001
            print(f"  {attempt} failed: {type(error).__name__}: {error}")

    if route is None:
        raise SystemExit(
            "No exporter available. Install one:\n"
            "  pip install ai-edge-torch ai-edge-litert\n"
            "or\n"
            "  pip install onnx2tf tensorflow"
        )

    size_mb = out_path.stat().st_size / (1024 * 1024)
    print(f"exported via {route}: {out_path} ({size_mb:.1f} MB)")

    (args.out_dir / "class_names.json").write_text(
        json.dumps(class_names, indent=2), encoding="utf-8"
    )
    print(f"class_names.json written ({len(class_names)} classes)")

    if args.verify:
        print("verifying against PyTorch")
        verify(model, out_path, args.image_size)

    print(
        "\nNext: copy the .tflite and class_names.json into the Android app's assets, and "
        "run the same preprocessing (224x224, ImageNet mean/std) on the device."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
