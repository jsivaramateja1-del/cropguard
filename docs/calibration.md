# Confidence calibration — measured findings

What the 0.75 expert-review gate actually does on the trained model, measured with
`ml_training/calibration_report.py` on the validation split (4,085 images, 16 classes).

## The run being measured

| | |
|---|---|
| Checkpoint | `cropguard_mobilenetv3.pth`, MobileNetV3-Large, 16 classes |
| Trained on | PlantVillage `color` (tomato + potato) + Rice Leaf Diseases, 20,432 images |
| Validation accuracy | 0.9976 |
| Macro recall | 0.9676 |

## Confidence distribution

```
mean confidence   : 0.9033
median            : 0.9069
p5                : 0.8804
p95               : 0.9250
```

98.0% of all predictions fall in the single bin 0.85–0.95. The distribution is
extremely narrow: this model almost never says "I am unsure" in a way the threshold
can see.

**Why it sits at 0.906.** The model was trained with `--label-smoothing 0.1` over 16
classes, so the training target for the correct class is
`0.9 + 0.1/16 = 0.90625`. A converged model reproduces its training target, and the
measured mean of 0.9033 is that number. The reported "confidence" is therefore close
to a learned constant rather than a calibrated probability of correctness. That is a
property of label smoothing, not a bug, but it matters when choosing a threshold.

## What the gate does

| threshold | routed to expert review | accuracy below | accuracy above | gap |
|---|---|---|---|---|
| 0.75 | 28 (0.7%) | 0.8571 | 0.9985 | **+0.1414** |
| 0.85 | 77 (1.9%) | 0.8831 | 0.9998 | +0.1166 |
| 0.90 | 1,066 (26.1%) | 0.9906 | 1.0000 | +0.0094 |

Read this carefully, because it cuts both ways:

* **The gate is informative.** Below 0.75 the model is right 85.7% of the time
  against 99.85% above — a 14-point gap. Low confidence genuinely means more errors.
* **The gate almost never fires.** 0.7% of predictions, 28 of 4,085.
* **Raising the threshold does not buy more signal.** At 0.90 you route 26% of
  reports, but those predictions are 99.06% accurate — the gap collapses to under one
  point. You would be sending good reports to experts for no reason.

There is no threshold that both fires often and separates errors well, because 98% of
predictions occupy a 0.10-wide band of confidence.

## Per-class behaviour

| crop | n | accuracy | mean confidence |
|---|---|---|---|
| Tomato | 3,631 | 0.9983 | 0.9035 |
| Potato | 430 | 1.0000 | 0.9063 |
| Rice | 24 | 0.8333 | 0.8202 |

Rice is the only crop where confidence drops meaningfully, and it is also the only
crop with real errors (`Rice___Leaf_smut` 0.75 accuracy, mean confidence 0.813). The
28 low-confidence predictions are therefore concentrated in rice, which makes sense:
120 field photographs from Gujarat against 20,000 lab images of tomato and potato.

## Decision

1. **Keep 0.75 as the automatic gate.** It is defensible and it catches the genuinely
   uncertain cases. Do not raise it to manufacture a busier expert queue.
2. **Add independent triggers for expert review**, because confidence alone will leave
   the queue nearly empty on in-distribution images:
   * the farmer explicitly requests an expert opinion;
   * the predicted class belongs to a different crop than the one the farmer selected
     (a real signal that the photo or the selection is wrong);
   * the uploaded image fails a basic quality check (too small, unreadable, extreme
     aspect ratio);
   * the predicted class is outside the model's 16 trained classes.
3. **Make the threshold configurable** (`CONFIDENCE_THRESHOLD` env var, default 0.75)
   so it can be re-chosen from data as more field images arrive.
4. **Do not describe confidence as calibrated probability** in the UI or the pitch. It
   is a model score, and this report shows it clusters near its training target.

## What would change this picture

Every number here comes from a random split of the same public datasets, so it
measures in-distribution behaviour. Two things would move it:

* **Field photographs.** PlantVillage is lab imagery on plain backgrounds. On phone
  photos of real leaves, accuracy will drop and confidence may *not* drop with it —
  the model's head is trained to output ~0.906 regardless. That failure mode is the
  strongest argument for the independent review triggers above.
* **Removing label smoothing.** Retraining without it would sharpen the distribution
  toward 0.99+ and make the 0.75 gate fire even less often. It would not improve
  separation, so there is no reason to.

## Reproducing

```bash
python ml_training/calibration_report.py \
    --checkpoint backend/model/cropguard_mobilenetv3.pth \
    --data-dirs "/kaggle/input/datasets/abdallahalidev/plantvillage-dataset/color" \
                "/kaggle/input/datasets/vbookshelf/rice-leaf-diseases/rice_leaf_diseases:Rice" \
    --crops Tomato Potato Rice
```
