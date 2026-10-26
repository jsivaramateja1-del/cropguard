# Architecture

How the pieces fit, and why they are built this way.

## Shape

```
   farmer's phone
   ┌──────────────────────────────┐
   │ React 18 + Vite + Tailwind   │        Vercel
   │ /            diagnose        │
   │ /outbreaks   map (lazy)      │
   │ /follow-up   farmer timeline │
   │ /expert      review queue    │
   └──────────────┬───────────────┘
                  │ HTTPS REST
   ┌──────────────▼───────────────┐
   │        FastAPI backend       │        Render (free, 0.1 CPU / 512 MB)
   │ auth · validation · routing  │
   └──┬────────┬─────────┬────────┘
      │        │         │
   ┌──▼─────┐ ┌▼──────┐ ┌▼──────────────┐
   │ ONNX   │ │Open-  │ │ Supabase      │
   │Mobile  │ │Meteo  │ │ Postgres      │
   │NetV3   │ │↓ MET  │ │ reports       │
   │16 cls  │ │Norway │ │ follow_ups    │
   └────────┘ └───────┘ │ audit_events  │
                        └───────────────┘
              images → Cloudinary (optional) or local disk
```

## Decisions and their reasons

**ONNX Runtime instead of PyTorch at serving time.** Measured on the target shape: the
PyTorch stack peaks at 660 MB, over the 512 MB limit, while ONNX Runtime needs 151 MB and
the whole API 211 MB. The exported graph was verified bit-identical to PyTorch
(`max abs diff 0.000e+00`), so nothing is lost. `requirements-deploy.txt` deliberately
omits torch; `requirements.txt` includes it for development.

**MobileNetV3-Large, fine-tuned not trained from scratch.** ImageNet-1K V2 weights, new
16-class head, all 4.2 M parameters trained. With ~20 k images and 12 epochs, a from-scratch
model would land far below the 0.9976 validation reached here, and the three rice classes
(120 images) could not be learned at all without pretrained features.

**Postgres, not SQLite, in production.** Render's disk is ephemeral, so an SQLite database
loses every report on each redeploy — which would also empty the outbreak map. Supabase
Postgres in `ap-south-1` (Mumbai) is the store. `create_all` never alters an existing table,
so `database._apply_additive_migrations()` adds new columns idempotently at startup.

**Anonymous farmers with scoped tokens.** Requiring an account to photograph a leaf would
kill adoption in the field. `POST /api/analyze` returns a one-time access token (hash
stored); the sequential id and the `CG-00042` reference are public and grant nothing. This
is the "unguessable, scoped report-access token" pattern, not a shortcut around auth.

**Two separate read paths.** `GET /api/reports` is a staff review queue holding names and
exact GPS. `GET /api/outbreaks` answers a different question — *where* is disease being
reported — and returns only rounded coordinates with counts. Weakening the first to serve a
public map would have leaked personal data; adding the second was the correct fix.

**Deterministic weather risk.** A documented 0–9 point table over temperature, humidity,
rain and wind, plus a per-disease adjustment, producing Low/Moderate/High/Critical with the
factors that drove it. Not a model, and not labelled as AI. Spray-window advice comes from
the same table (wind and rain thresholds), so it can be audited.

**Advisory without dosages.** 21 entries covering the canonical classes; viruses and mites
are marked `recommended: false` for chemical control because no product cures them. Where
authoritative dosage data was unavailable, none was invented — the UI directs the farmer to
the product label and the local extension service.

**Crop capability comes from the model, not the UI.** `_model_supported_crops()` reads the
loaded artifact's labels. Wheat appears in the advisory DB and in the original crop list but
has no trained classes, so the backend refuses it with `422` and an explanation, rather than
returning a tomato diagnosis for a wheat photo.

**Lazy-loaded Leaflet.** The map bundle is 48 kB gzipped; loading it on the diagnosis page
would slow the first paint on a rural connection for no benefit. `/outbreaks`, `/follow-up`
and `/expert` are all `React.lazy` routes.

**SPA rewrites in `vercel.json`.** Without them a nested route 404s on direct load or
refresh — caught by the live verification run, not by any unit test.

## Request flow: one diagnosis

1. `POST /api/analyze` — rate limit per client, declared media type, then decode with PIL
   and check the **actual** format.
2. Foliage plausibility over a 96×96 downsample: green plus yellow/brown dying tissue.
3. ONNX inference → 16 logits → softmax → top-3.
4. Status decision: confidence < 0.75, confidence < 0.88 (outside the leaf range), crop
   mismatch, implausible photo, or farmer request → `Pending_Expert_Review`, each with a
   named reason.
5. Advisory attached only if the predicted crop matches the selected crop.
6. Weather fetched for the coordinates (10-minute cache), risk computed and stored.
7. Report persisted, `CG-00042` reference assigned, access token returned once.

## Failure behaviour

| Failure | Behaviour |
|---|---|
| Database unreachable at startup | API still starts; `/health` reports `unhealthy` with a reason code and hint; storage endpoints return `503` |
| Model missing | `/health` `unhealthy`; `/api/analyze` `503` with the load error; `/api/model` `503` |
| Open-Meteo rate-limits | MET Norway fallback; `source` in the response says which answered |
| Both weather providers down | `available: false`; the report is still created |
| Cloudinary unset | Local disk, and the response says images will not persist |
| Upload unreadable / wrong format | `415` before any inference |

## Testing

141 backend tests (`backend/tests/`), 14 frontend error-handling checks, 21 render checks.
Four CI jobs on every push: `backend-tests`, `contract` (class list vs advisory DB),
`syntax`, `frontend`. The contract job exists because a 16-class model with a 21-entry
advisory DB is exactly where silent drift hides.
