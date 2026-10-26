# CropGuard — ML training

Everything needed to train, evaluate and export CropGuard's crop-disease classifier.

The pipeline is deliberately boring and explicit: it discovers what is actually on disk,
refuses to train on a class with no images, reports what it mapped and what it skipped, and
exports one checkpoint plus one label file that the backend loads without any hard-coded
class list.

```
ml_training/
├── cropguard_common.py            shared: canonical labels, model factory, transforms, checkpoint I/O
├── train_kaggle.py                train → validate → export (Kaggle GPU)
├── generate_starter_model.py      development artifact so the backend can be wired before training
├── class_names_canonical.json     the 21 supported labels (single source of truth)
└── README.md
```

Keep these files in the same directory — `train_kaggle.py` and `generate_starter_model.py`
both import `cropguard_common.py`.

---

## 1. The model

| Property | Value |
|----------|-------|
| Architecture | MobileNetV3-Large (`torchvision.models.mobilenet_v3_large`) |
| Initialisation | ImageNet-1K V2 weights, classifier replaced for N classes |
| Input | RGB, 224×224, ImageNet normalisation (mean `0.485/0.456/0.406`, std `0.229/0.224/0.225`) |
| Train augmentation | RandomResizedCrop(0.65–1.0), h/v flip, ±20° rotation, colour jitter |
| Val / inference | Resize 256 → CenterCrop 224 |
| Loss | CrossEntropy, label smoothing 0.1, optional inverse-frequency class weights |
| Optimiser | AdamW, lr 3e-4, weight decay 1e-4, cosine annealing |
| Precision | AMP (fp16) on CUDA |
| Selection | Best validation accuracy checkpoint, early stopping (patience 4) |

Roughly 4.2 M parameters (~2.5 M in the backbone). It is small on purpose: it must run
comfortably on a free CPU tier of a Hugging Face Space or Render instance.

## 2. Supported classes

`class_names_canonical.json` defines the 21 labels across Tomato, Rice, Potato and Wheat.
Dataset folder names are matched to these labels after normalising case and punctuation, so
`Tomato___Early_blight`, `Tomato-Early blight` and `tomato_early_blight` all map to
`Tomato___Early_blight`. Folders that match nothing are reported and skipped — never
silently folded into another class.

If a supported class has **no** images, training aborts with the list of missing classes.
Override with `--allow-missing-classes` only when you consciously want a smaller model.

## 3. Datasets

Public sources that cover the label list (Kaggle, free account required):

| Crop | Dataset | Folder layout | Notes |
|------|---------|---------------|-------|
| Tomato, Potato | [PlantVillage Dataset](https://www.kaggle.com/datasets/abdallahalidev/plantvillage-dataset) `abdallahalidev/plantvillage-dataset` | `plantvillage dataset/color/Tomato___Early_blight/*.JPG` | 38 classes; use the `color` variant. Covers all 10 tomato and all 3 potato labels. |
| Rice | [Rice Leaf Diseases](https://www.kaggle.com/datasets/vbookshelf/rice-leaf-diseases) `vbookshelf/rice-leaf-diseases` | `Bacterial leaf blight/`, `Brown spot/`, `Leaf smut/` | 120 real field images from Gujarat. Folders omit the crop name — see the crop hint below. |
| Rice (larger) | [Rice Plant diseases dataset](https://www.kaggle.com/datasets/jay7080dev/rice-plant-diseases-dataset) `jay7080dev/rice-plant-diseases-dataset` | `Bacterialblight/`, `Brownspot/`, `Leafsmut/` | ~4,700 images, same three classes, also needs a crop hint. |
| Wheat | search "wheat leaf disease" on Kaggle | varies by uploader | Yellow rust / Brown rust / Septoria are not in PlantVillage, so wheat needs a separate dataset. Verify the folder names with `--inspect-only` before training — I have not validated a specific slug. |

Attach datasets to the notebook via *Add Input*, then pass every root with `--data-dirs`.
Multiple roots are merged; identical canonical labels are combined, so you can layer field
images on top of PlantVillage.

### Finding the mounted paths

Kaggle mounts an attached dataset under its **slug**, not its display title, and the prefix
varies by environment — you may see `/kaggle/input/<slug>/` or
`/kaggle/input/datasets/<owner>/<slug>/`. Never hardcode either; search the tree:

```python
import glob, os
hits = sorted(glob.glob("/kaggle/input/**/Tomato___Early_blight", recursive=True))
print(hits[:4])
rice = sorted(glob.glob("/kaggle/input/**/Brown spot", recursive=True))
print(rice[:2])
```

Two things to watch for in the result:

* **PlantVillage ships duplicate copies.** The mirror can contain both
  `<dataset>/color/` and `<dataset>/plantvillage dataset/color/`, alongside `grayscale/` and
  `segmented/`. Point `--data-dirs` at a single `color/` directory. Pointing at the dataset
  root trains on the same leaf several times over in different renderings, which inflates the
  image count and quietly hurts the model. The script warns when it sees multiple variants
  directly under a root.
* **Rice nests one level deeper:** `<dataset>/rice_leaf_diseases/Brown spot`, so the root to
  pass is the `rice_leaf_diseases` directory.

A missing root now reports the nearest existing directory, its contents, and same-named
directories that do exist, so a wrong path is one run away from being obvious.

### Crop hint for folders without a crop name

PlantVillage folders carry the crop (`Tomato___Early_blight`), but the rice and many wheat
datasets name folders by disease only (`Brown spot`). Those cannot be mapped on their own, so
append `:Crop` to that root:

```bash
--data-dirs "/kaggle/input/rice-leaf-diseases:Rice"
```

`Brown spot` in a `:Rice` root becomes `Rice___Brown_spot`. The hint is used only when the
bare folder name fails to match, so PlantVillage roots need no hint. If a folder is skipped
and a crop hint would have rescued it, the script says so explicitly.

Always inspect first — it costs seconds and prints exactly what will be trained:

```bash
python train_kaggle.py --data-dirs "/kaggle/input/plantvillage-dataset/plantvillage dataset/color" \
                       "/kaggle/input/rice-leaf-diseases:Rice" --inspect-only
```

Expect a line per root with its class-dir and image counts, then a per-class coverage table.
Skipped folders and untrained classes are listed; nothing is silently dropped.

## 4. Training on Kaggle

1. Create a notebook, set **Accelerator → GPU T4 x2** (or P100) and **Internet → On**.
2. Add the datasets above via *Add Input*.
3. Run:

```python
!git clone https://github.com/jsivaramateja1-del/cropguard.git
%cd cropguard/ml_training
!python train_kaggle.py \
    --data-dirs "/kaggle/input/plantvillage-dataset/plantvillage dataset/color" \
                "/kaggle/input/rice-leaf-diseases:Rice" \
    --epochs 12 --batch-size 64 --num-workers 2 \
    --out-dir /kaggle/working/export
```

That trains the 13 PlantVillage tomato/potato classes plus the 3 rice classes (16 of the 21).
Adding a wheat dataset brings it to 21. Roughly 40–70 minutes on a T4.

Useful flags: `--lr`, `--val-split`, `--dropout`, `--class-weights`, `--no-pretrained`,
`--offline`, `--seed`, `--early-stop-patience`, `--crops Tomato Rice` (subset).

### What the run prints

```
[1/6] Discovering datasets
  - /kaggle/input/.../color: 38 class dirs, 54305 images
  ! 17 dataset folder(s) do not map to a supported class and were skipped: Apple___Apple_scab, ...
[2/6] Splitting train/validation
  train: 17336 images
  val  :  4341 images
[3/6] Building MobileNetV3-Large
[4/6] Training for up to 12 epochs (AMP: on)
  epoch  1/12  train_loss 1.4821  train_acc 0.5812  val_loss 0.6221  val_acc 0.8104  macro_recall 0.7731  (214s)
    -> new best (val_acc 0.8104) saved to /kaggle/working/export/cropguard_mobilenetv3.pth
...
[6/6] Summary
  best val accuracy   : 0.9xxx
```

Then per-class accuracy, the most frequent confusions, and the explicit list of classes the
model can predict.

## 5. Exported artifacts

| File | Content |
|------|---------|
| `cropguard_mobilenetv3.pth` | Torch checkpoint dict: `state_dict`, `class_names`, `num_classes`, `image_size`, `normalization`, `artifact_type`, `val_accuracy`, `per_class_accuracy`, `created_at` |
| `class_names.json` | Ordered label list; index *i* is the class index of output neuron *i* |
| `training_log.csv` | One row per epoch |
| `training_metrics.json` | Full report: history, per-class accuracy, confusions, config |

Copy the first two into the backend:

```bash
cp /kaggle/working/export/cropguard_mobilenetv3.pth \
   /kaggle/working/export/class_names.json \
   backend/model/
```

Verify the export on a real photo before trusting it:

```bash
python train_kaggle.py --predict /path/to/leaf.jpg --out-dir /kaggle/working/export
```

### Checking the confidence gate

Validation accuracy alone does not tell you whether the 0.75 expert-review threshold
works. `calibration_report.py` measures that directly on the same split the training
run used:

```bash
python calibration_report.py \
    --checkpoint /kaggle/working/export/cropguard_mobilenetv3.pth \
    --data-dirs "/kaggle/input/datasets/abdallahalidev/plantvillage-dataset/color" \
                "/kaggle/input/datasets/vbookshelf/rice-leaf-diseases/rice_leaf_diseases:Rice" \
    --crops Tomato Potato Rice
```

It prints the confidence distribution, the share of predictions below 0.75 / 0.85 /
0.90, a reliability table (confidence bin vs observed accuracy), per-class mean
confidence, and — the number that matters — the accuracy gap between predictions
above and below 0.75. A large gap means the gate separates real errors from reliable
ones. No gap means the threshold is decorative and should be re-chosen from data.

## 5b. Fine-tuning a Hugging Face model instead

`train_hf.py` trains a Hugging Face image classifier on the same 16 labels, using the
same dataset discovery and split as `train_kaggle.py`, and exports the same ONNX
contract. The backend needs no change: it loads whatever ONNX + `class_names.json` pair
is in `backend/model/`.

Why not just download a model from the Hub? Every plant-disease model there is a
38-class PlantVillage model — tomato and potato, **no rice**, and label strings that do
not match ours (`"Apple Scab"` rather than `Crop___Condition`). The backend deliberately
refuses a graph whose output width disagrees with the label file, so adopting one
directly is not possible without a mapping layer. Fine-tuning one as a *starting point*
avoids all of that.

Measured candidates (ONNX Runtime, 2 vCPU, this machine):

| Base model | ONNX | Peak RAM | Inference | Notes |
|---|---|---|---|---|
| `linkanjarad/mobilenet_v2_1.0_224-plant-disease-identification` | ~9 MB | ~180 MB | ~30 ms | default; keeps free-tier speed |
| `rodynaemad/plant-disease-dinov2-small` | 85 MB | 274 MB | 618 ms | better features, 31x slower |
| ViT-base plant models | 343 MB | won't fit 512 MB | — | not usable on Render Free |

For reference, the shipped MobileNetV3 is 16.8 MB, 151 MB peak, 19.9 ms.

```bash
python train_hf.py \
    --data-dirs "/kaggle/input/plantvillage-dataset/color" \
                "/kaggle/input/rice-leaf-diseases:Rice" \
    --crops Tomato Potato Rice --allow-missing-classes \
    --epochs 8 --batch-size 64 --out-dir /kaggle/working/export_hf
```

Swap the base with `--model rodynaemad/plant-disease-dinov2-small` for the accuracy
option, or `--freeze-backbone` to train only the new head.

Keep whichever model wins. The shipped MobileNetV3 scores 0.9976 validation and 36/36
on labelled photos, so a replacement has to beat that to be worth the swap.

## 6. Development artifact (no GPU needed)

```bash
python generate_starter_model.py --verify
```

This writes a checkpoint with the correct architecture, the real 21-class label list and a
**randomly initialised head**. It exists so the backend's load → preprocess → softmax →
threshold path can be built and tested before training finishes.

It cannot diagnose anything. Its scores sit near uniform (1/21 ≈ 0.048), so every report
made against it falls below the 0.75 expert-review threshold and routes to
`Pending_Expert_Review` — which is the correct behaviour for an untrained model. The
checkpoint is tagged `artifact_type: "development_untrained"`; the backend reads that tag and
will not present its output as a diagnosis.

Overwrite it with the real export before any demo. `generate_starter_model.py` refuses to
overwrite a checkpoint tagged `trained` unless you pass `--force`.

## 7. Troubleshooting

**`Temporary failure in name resolution` / `Could not resolve host: github.com`**
The notebook has no internet. Two consequences: `git clone` fails, and torchvision cannot
fetch the ImageNet weights. The script no longer crashes on the second one — it prints a
warning, falls back to random initialisation and keeps training, which is fine for a wiring
test but not for a real run. Fix it one of two ways:

* Turn internet on: **Settings → Internet → On** (needs a phone-verified Kaggle account),
  then re-run. Confirm with `!curl -s -o /dev/null -w "%{http_code}\n" https://download.pytorch.org`
  — expect `200`.
* Stay offline and skip the download attempt entirely: add `--offline`. Training then runs
  from scratch and needs many more epochs to reach useful accuracy.

Either way `training_metrics.json` and the checkpoint record `backbone_pretrained`, so an
export never overstates what it was trained from.

**`torch 2.x+cpu` and `device: cpu`**
No accelerator is attached. Set **Settings → Accelerator → GPU T4 x2**. A GPU session reports
the CUDA build of torch and `device: cuda (Tesla T4)` with `AMP: on`.

**`No such file or directory: /kaggle/input/<your-dataset-slug>/...`**
That was a literal placeholder. Find the real path instead of guessing:

```python
import glob, os
hits = glob.glob("/kaggle/input/*/**/train_kaggle.py", recursive=True)
print(hits); os.chdir(os.path.dirname(hits[0])); print(os.getcwd())
```

**Writes fail under `/kaggle/input`**
Attached datasets are mounted read-only. Always pass `--out-dir /kaggle/working/export`.

**`Training aborted: no images found for these supported classes`**
A supported class has no images in the roots you passed. Add the dataset, narrow with
`--crops`, or use `--allow-missing-classes` deliberately. Run `--inspect-only` first to see
what was actually discovered.

## 8. Honest limitations

* PlantVillage images are lab-captured on plain backgrounds. Accuracy on phone photos taken
  in a field will be lower; add field datasets and re-train before judging real-world
  performance.
* 21 classes, four crops. The model is not a general plant-disease detector.
* No pest detection beyond the two-spotted spider mite class inherited from PlantVillage.
* Reported validation accuracy is measured on a held-out split of the same datasets — it is
  not a field-performance claim.
