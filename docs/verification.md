# Verification: does the deployed system do what it was built for?

An independent check of the **artifact actually deployed**, not of a local copy.

Method: downloaded `backend/model/cropguard_mobilenetv3.onnx` and `class_names.json`
straight from the repository (the files Render serves), then ran them through
`backend/inference.py` against 36 labelled photographs from the PlantVillage colour
set — 3 images for each of the 12 supported tomato and potato classes.

## Result

```
ACCURACY: 36/36 = 100.0%

confidence: min 0.823  median 0.907  max 0.935
mean inference: 19.9 ms per image (2 vCPU, ONNX Runtime)
plausibility check passed: 36/36   (real leaves, as expected)
```

Per class, 3/3 each: Tomato Bacterial spot, Early blight, Late blight, Leaf mould,
Septoria leaf spot, Target spot, Tomato mosaic virus, Tomato yellow leaf curl virus,
Tomato healthy; Potato Early blight, Late blight, healthy.

This confirms four things that a wiring bug would have broken:

1. **The deployed artifact is the trained one.** Label order matches the graph — a
   mismatch is refused at load, and would have shown as nonsense predictions.
2. **Preprocessing matches training.** The API's PIL + NumPy pipeline produces the same
   normalised input the model expects; a resize or normalisation mismatch degrades
   accuracy to chance, not to 100%.
3. **The ONNX export is faithful.** Earlier the exporter verified
   `max abs diff 0.000e+00` against the PyTorch graph; this is the end-to-end
   confirmation on real images.
4. **The advisory chain resolves.** Each predicted label has a knowledge-base entry
   (`scripts/check_contract.py` enforces 21-for-21), so every prediction returns advice.

## What this does NOT prove

- **It is in-distribution.** These are PlantVillage images, the same source the model
  trained on. Several are likely in the training set. This is a check that the deployed
  pipeline works, not a measurement of generalisation.
- **It is not field photography.** A phone photo of a real field leaf will score lower
  and be wrong more often. That number does not exist yet and must not be invented.
- **Rice is untested here.** PlantVillage has no rice classes; the three rice classes
  were trained on 120 field images from Gujarat and are known to be weaker (0.75–0.88
  validation accuracy per class).
- **Three images per class is a small sample.** Enough to catch a broken pipeline,
  not enough to estimate accuracy to a percentage point.

## Operational behaviour observed

- **3 of 36** genuine leaf photos scored below the 0.88 in-distribution floor and would
  be routed to expert review — the two lowest were `Tomato___Tomato_mosaic_virus` at
  0.823 and 0.857. That is the intended trade: a small share of correct results goes to
  a human so that out-of-range scores do not get auto-verified.
- **0 of 36** fell below the 0.75 confidence threshold.
- Weather risk, MET Norway fallback, report persistence, expert verification and
  follow-up endpoints were verified separately against the live service.

## Reproduce

```bash
# from backend/, with the deployed artifacts already in model/
python -m pytest tests -v            # 106 tests
python /path/to/verify_script.py     # downloads images and scores the model
```
