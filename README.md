# CropGuard

**Early detection and management of crop diseases and pest infestations.**

CropGuard is a decision-support tool for smallholder farmers: a farmer photographs a
diseased leaf, the system screens it against an on-device-sized vision model, cross-checks
the local weather for disease-favourable conditions, returns a practical advisory, and
places the case on a district-level outbreak map where an agricultural expert can confirm
or correct the diagnosis. The farmer is then prompted to follow up as the crop recovers.

Built for **Frontier Demo Day 2026**. The problem statement is inspired by
SIH26131 (*Early Detection and Management of Crop Diseases and Pest Infestations*);
CropGuard is an independent prototype, not an SIH submission.

> CropGuard is an early-screening aid, not a replacement for an agricultural expert.
> AI results are presented as a *potential diagnosis* and must be confirmed by a human
> when confidence is low.

---

## Build status

This repository is being built in seven phases. What exists today is listed honestly
below — nothing is stubbed, and no phase is presented as finished before it runs.

| Phase | Scope | Status |
|-------|-------|--------|
| 1 | Monorepo + Kaggle PyTorch training foundation | **complete** |
| 2 | Backend database + advisory knowledge base | not started |
| 3 | AI inference + weather risk + FastAPI | not started |
| 4 | React diagnosis application | not started |
| 5 | Outbreak monitoring map | not started |
| 6 | Expert verification + follow-up | not started |
| 7 | Deployment-ready configuration (HF / Render / Vercel) | not started |

Directories for later phases (`backend/`, `frontend/`, `docs/`) exist as placeholders in
the repository skeleton and are filled in as each phase lands.

---

## Architecture (target)

```
                    ┌──────────────────────────────┐
   farmer's phone   │  React 18 + Vite + Tailwind  │
   (Android Chrome) │  camera / upload / Leaflet   │
                    └──────────────┬───────────────┘
                                   │ HTTPS REST (JSON)
                    ┌──────────────▼───────────────┐
                    │        FastAPI backend       │
                    │  validation · routing · CORS │
                    └──┬────────┬────────┬─────────┘
                       │        │        │
        ┌──────────────▼──┐  ┌──▼─────┐ ┌▼──────────────┐
        │ PyTorch         │  │ Open-  │ │ Advisory      │
        │ MobileNetV3-L   │  │ Meteo  │ │ knowledge base│
        │ (21 classes)    │  │ weather│ │ (JSON)        │
        └─────────────────┘  └────────┘ └───────────────┘
                       │        │        │
        ┌──────────────▼────────▼────────▼──────────────┐
        │ Supabase PostgreSQL · Cloudinary image store  │
        └───────────────────────────────────────────────┘
```

Deployment targets: **Hugging Face Spaces** (primary backend) or **Render** (fallback),
**Vercel** (frontend), **Supabase** (PostgreSQL), **Cloudinary** (leaf images),
**Kaggle** (GPU training).

---

## Technology stack

| Layer | Choice |
|-------|--------|
| Vision model | MobileNetV3-Large (torchvision), PyTorch, 224×224, ImageNet normalisation |
| Training | Kaggle Notebook, free GPU (T4/P100), mixed precision |
| Backend | FastAPI, SQLAlchemy 2.x, Pydantic v2, Uvicorn |
| Database | PostgreSQL on Supabase; SQLite for local development |
| Images | Cloudinary (production), `backend/uploads/` (local fallback) |
| Weather | Open-Meteo forecast API (keyless) |
| Frontend | React 18, Vite, Tailwind CSS, React Router, Axios, Lucide, Leaflet |

---

## Repository structure

```
cropguard/
├── backend/                  # FastAPI service (Phase 2+)
│   ├── model/                # cropguard_mobilenetv3.pth + class_names.json land here
│   └── uploads/              # local image fallback for development
├── frontend/                 # React application (Phase 4+)
├── ml_training/              # training pipeline (Phase 1 — complete)
│   ├── cropguard_common.py       # shared labels, model factory, checkpoint format
│   ├── train_kaggle.py           # GPU training + evaluation + export
│   ├── generate_starter_model.py # development artifact for backend wiring
│   ├── class_names_canonical.json# single source of truth for the 21 classes
│   └── README.md
├── docs/                     # architecture, API and ML documentation (Phase 7)
├── .gitignore
├── README.md
└── LICENSE
```

---

## Supported classes (model scope)

The classifier is trained on **21 labels across four crops**. `ml_training/class_names_canonical.json`
is the single source of truth: the training script maps dataset folders onto these labels,
and the advisory knowledge base must contain exactly one entry per label.

| Crop | Conditions |
|------|-----------|
| Tomato (10) | Bacterial spot, Early blight, Late blight, Leaf mould, Septoria leaf spot, Spider mites (two-spotted), Target spot, Tomato mosaic virus, Tomato yellow leaf curl virus, Healthy |
| Rice (4) | Bacterial leaf blight, Brown spot, Leaf smut, Healthy |
| Potato (3) | Early blight, Late blight, Healthy |
| Wheat (4) | Yellow rust, Brown rust, Septoria, Healthy |

**The model cannot identify anything outside this list.** A confident-looking answer for an
unsupported crop is not a feature — the backend reports low confidence and routes the case
to expert review.

---

## Local setup — ML training

The training pipeline is the Phase 1 deliverable. It runs on CPU locally for smoke tests
and on a Kaggle GPU for real training.

```bash
cd ml_training
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install torch torchvision pillow numpy tqdm
```

Create the development artifact so the backend can be wired up before a GPU run exists:

```bash
python generate_starter_model.py --verify
```

Inspect a dataset before committing GPU hours to it:

```bash
python train_kaggle.py --data-dirs /path/to/plantvillage/color --inspect-only
```

Full instructions, dataset links and the export contract are in
[`ml_training/README.md`](ml_training/README.md).

---

## Training on Kaggle

```python
# Kaggle notebook, GPU accelerator + Internet ON
!git clone https://github.com/jsivaramateja1-del/cropguard.git
%cd cropguard/ml_training
!python train_kaggle.py \
    --data-dirs "/kaggle/input/plantvillage-dataset/plantvillage dataset/color" \
                "/kaggle/input/rice-leaf-diseases:Rice" \
    --epochs 12 --batch-size 64 --out-dir /kaggle/working/export
```

Note the `:Rice` suffix: that dataset names its folders by disease only (`Brown spot`), so
the crop has to be supplied. PlantVillage needs no hint.

The run exports `cropguard_mobilenetv3.pth`, `class_names.json`, `training_log.csv` and
`training_metrics.json`. Copy the first two into `backend/model/`.

---

## Environment variables

Introduced in Phase 2, documented in `backend/.env.example`. Planned keys:

```
DATABASE_URL=          # PostgreSQL (Supabase) — falls back to SQLite when unset
SUPABASE_URL=
SUPABASE_ANON_KEY=
CLOUDINARY_CLOUD_NAME=
CLOUDINARY_API_KEY=
CLOUDINARY_API_SECRET=
MODEL_PATH=
FRONTEND_ORIGIN=
```

No secret is ever committed. Only `.env.example` is tracked.

---

## Model safety

* Output is labelled **Potential diagnosis**, never a confirmed result.
* Confidence below **0.75** routes the report to `Pending_Expert_Review`.
* Every advisory repeats: follow the product label, follow local agricultural authority
  guidance, and consult an expert when uncertain. Dosages are never invented.
* The development artifact in `backend/model/` is tagged `artifact_type: development_untrained`
  and cannot produce a trustworthy prediction by construction.

---

## Limitations (prototype)

* Trained on public research datasets; field accuracy on Indian farms is not yet measured.
* 21 classes, four crops. Everything else is out of scope.
* No user accounts yet — the farmer identifies a report by its ID.
* Weather risk is a deterministic index over temperature, humidity and rainfall, not a
  disease-epidemic model.
* Advisory text is general guidance, not a prescription.

## Future improvements

* Field-image fine-tuning with agronomist-labelled data from partner districts.
* Per-region epidemic models using historical outbreak + weather series.
* Offline inference (ONNX / TFLite) so the app works without connectivity.
* Multilingual UI and SMS/IVR advisory delivery.
* Authenticated expert accounts with district scoping and audit trails.

---

## License

MIT — see [LICENSE](LICENSE).
