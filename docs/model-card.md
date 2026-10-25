# CropGuard model card

A one-page, honest assessment of the vision model, written the way a reviewer would
interrogate it.

## Identity

| | |
|---|---|
| Architecture | MobileNetV3-Large, ImageNet-1K V2 pretrained, classifier replaced for 16 classes |
| Parameters | 4,222,528 |
| Input | 224×224 RGB, ImageNet normalisation |
| Artifacts | `cropguard_mobilenetv3.pth` (PyTorch, 21 MB), `cropguard_mobilenetv3.onnx` (16.8 MB) |
| CPU inference | ~21 ms/image measured on 2 vCPU; ~0.2–0.5 s expected on Render's 0.1 CPU |
| Peak memory | 660 MB with PyTorch, 151 MB with ONNX Runtime |
| Trained | Kaggle, Tesla T4, 12 epochs, 24.6 minutes |

## Training data

| Crop | Source | Classes | Images |
|---|---|---|---|
| Tomato | PlantVillage `color` | 10 | 18,160 |
| Potato | PlantVillage `color` | 3 | 2,152 |
| Rice | Rice Leaf Diseases (Gujarat field photos) | 3 | 120 |
| **Total** | | **16** | **20,432** |

Validation split: 4,085 images (20%, stratified, seed 42).

Not covered: wheat entirely (four supported classes have no dataset), and
`Rice___healthy`. The model cannot predict any of those five.

## Measured performance

| Metric | Value |
|---|---|
| Validation accuracy | 0.9976 |
| Macro recall | 0.9676 |
| Mean confidence | 0.9033 |
| Predictions below 0.75 | 0.7% |
| Accuracy gap above/below 0.75 | +0.1414 |

Per crop: tomato 0.9983, potato 1.0000, rice 0.8333.

## Rating

Scores are out of 10 and deliberately split, because a single number would hide the
one thing that matters most.

| Dimension | Score | Reasoning |
|---|---|---|
| Fit to benchmark | **9.5** | 0.9976 on a random PlantVillage split is at the level published work reports for this dataset. It confirms the pipeline is correct rather than that the model is extraordinary. |
| Pipeline correctness | **9.5** | Label mapping, stratification, augmentation, AMP training, checkpoint selection and export all verified end to end. Confusions are visually plausible pairs, not noise. |
| Class coverage | **6.0** | 16 of 21 supported classes, 3 of 4 crops. Wheat absent, healthy rice absent. |
| Weak-class handling | **5.5** | Rice: 0.8333 accuracy from 120 field images against 20,000 lab images elsewhere. `Rice___Leaf_smut` at 0.75 with mean confidence 0.813. |
| Confidence calibration | **5.0** | Mean confidence 0.9033 sits on the label-smoothing target of 0.90625. The score clusters tightly and will not reliably flag a wrong field diagnosis. See `docs/calibration.md`. |
| Field generalisation | **not measurable** | Every number comes from lab photography on plain backgrounds. A random split also leaks near-duplicate leaves across train and validation. No field data has been collected, so any claim here would be invented. |
| Efficiency | **9.5** | 16.8 MB ONNX, 21 ms on 2 vCPU, 151 MB peak — fits a free 512 MB host with room to spare. |
| Demo readiness | **8.5** | Reliable for a live demonstration on tomato and potato. Rice is honest but visibly weaker. |

**Overall: 8/10 as a prototype, 9.5/10 on its own benchmark, unproven in the field.**
That last clause is the one to say out loud at Demo Day.

## Intended use

Early screening aid for tomato, potato and rice leaves photographed on a phone.
The output is a *potential diagnosis* that routes to human confirmation when
confidence is low or the situation is ambiguous.

## Out of scope

* Any crop other than tomato, potato and rice.
* Wheat, and healthy rice — not trained, will be misclassified if submitted.
* Pest identification beyond two-spotted spider mite.
* Diagnosis from a photo of soil, fruit, stem or whole-field canopy.
* Any decision made without expert confirmation when confidence is low.

## Known limitations

1. **Lab-to-field gap.** Training images have uniform backgrounds; field photos do
   not. Expect accuracy to drop and confidence to drop less.
2. **Split leakage.** PlantVillage contains multiple images of the same leaf, so a
   random split overstates generalisation.
3. **Confidence is not calibrated.** It is a model score near the label-smoothing
   target, not a probability of correctness.
4. **Rice is data-starved.** 40 images per class; validation per-class accuracy moves
   in 12.5-point steps.
5. **Class imbalance** in tomato (5,357 yellow-leaf-curl against 373 mosaic virus).

## What would raise the rating

* 200–500 farmer photographs per crop, labelled with an agronomist, then fine-tune.
* A wheat dataset covering yellow rust, brown rust and Septoria.
* Group-aware splitting (by leaf, not by image) to remove leakage from the reported
  number.
* Re-fit the confidence threshold on field data, or add a top-2 margin and an
  out-of-distribution check.
* Calibration against a held-out field set, reported as a reliability diagram.
