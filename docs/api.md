# CropGuard API

Base URL: `https://cropguard-pzs9.onrender.com` · interactive docs at `/docs`

All request and response bodies are JSON except `POST /api/analyze` and
`POST /api/reports/{id}/followup`, which take `multipart/form-data`.

## Authentication

Two mechanisms, for two kinds of caller.

**Farmers are anonymous.** `POST /api/analyze` returns an `access_token` exactly once. Only
its SHA-256 hash is stored. Send it as the `X-Report-Token` header to read that report or
add a follow-up. The report id and the `CG-00042` reference are public and grant nothing.

**Staff use a bearer token.** `EXPERT_TOKEN` (or `ADMIN_TOKEN`) configured on the server,
sent as `Authorization: Bearer <token>`. The expert name in a request body is recorded as a
note and never authorises anything.

| Status | Meaning |
|---|---|
| `401` | Missing or invalid credentials |
| `403` | Authenticated but not permitted |
| `404` | Not found **or** not yours — deliberately indistinguishable, so ids cannot be probed |
| `415` | Wrong media type (declared or actually decoded) |
| `422` | Valid request, unsupported value (e.g. a crop the model has no classes for) |
| `429` | Rate limited, with `Retry-After` |
| `503` | Database or model unavailable |

---

## `GET /health`

Component-level readiness. No filesystem paths, hostnames, credentials or stack traces.

```json
{
  "status": "ok",
  "version": "0.3.0",
  "checks": {
    "database": { "ok": true, "dialect": "postgresql", "initialised": true, "reason": null, "hint": null, "host": { "applies": false } },
    "model":    { "ok": true, "backend": "onnx", "classes": 16, "artifact_type": "trained", "error": null },
    "storage":  { "backend": "local", "writable": true },
    "weather":  { "providers": ["open-meteo", "met-no"], "cached_locations": 0, "api_key_configured": false },
    "auth":     { "staff_configured": true }
  },
  "thresholds": { "confidence": 0.75, "ood_floor": 0.88 },
  "advisory_entries": 21
}
```

`status` is `unhealthy` when the database or the model is unavailable — the model is
essential, so a service that cannot diagnose anything does not report itself healthy.

When the database is down, `reason` is one of `host_not_found`, `no_route`,
`auth_failed`, `connection_refused`, `timeout`, `tls_error`, `too_many_connections`,
`password_not_url_encoded`, `placeholder_not_replaced`, `query_failed`, each with a `hint`.
`host` reports whether the configured hostname resolves and to which address family —
`ipv4`, `ipv6_only` or `none` — which separates a typo from an IPv6-only host.

## `GET /api/model`

The deployed model's contract. Public: a farmer has a right to know what it was trained on.

```json
{
  "model_version": "…",
  "artifact_type": "trained",
  "backend": "onnx",
  "classes": ["Potato___Early_blight", "…"],
  "output_dimensions": 16,
  "image_size": 224,
  "channel_order": "RGB",
  "normalization": { "mean": [0.485, 0.456, 0.406], "std": [0.229, 0.224, 0.225] },
  "supported_crops": ["Potato", "Rice", "Tomato"],
  "classes_by_crop": { "Tomato": ["…"] },
  "healthy_class_available": { "Potato": true, "Rice": false, "Tomato": true },
  "confidence_threshold": 0.75,
  "ood_confidence_floor": 0.88,
  "provenance": { "artifact": "cropguard_mobilenetv3", "trained_on": "…" }
}
```

The frontend reads this instead of assuming a crop in a dropdown is supported. `503` if the
model is not loaded.

## `POST /api/analyze`

`multipart/form-data`. Rate limited per client (default 20 per 60 s).

| Field | Type | Notes |
|---|---|---|
| `image` | file | JPEG, PNG or WebP, ≤ 8 MB; the **decoded** format is checked, not the filename |
| `crop_type` | text | Must be a crop the model has classes for |
| `farmer_name` | text | ≥ 2 characters |
| `latitude`, `longitude` | text | Validated ranges |
| `region_name` | text | Optional |
| `request_expert_review` | bool | Optional; forces review |

Response (abridged):

```json
{
  "report": {
    "id": 2, "reference": "CG-00002",
    "predicted_disease": "Tomato___Late_blight",
    "confidence": 0.68,
    "status": "Pending_Expert_Review",
    "created_at": "2026-10-26T11:44:00+00:00"
  },
  "access_token": "…returned once…",
  "plausibility": { "is_plausible_leaf": true, "vegetation_fraction": 0.42, "threshold": 0.15 },
  "advisory": { "…": "null when the predicted crop differs from the selected crop" },
  "review_required": true,
  "review_reasons": ["Confidence 0.68 is below the 0.75 threshold…"],
  "warnings": []
}
```

Status routing:

- `AI_Verified` — confidence ≥ threshold, crop matches, photo plausibly a leaf
- `Pending_Expert_Review` — low confidence, crop mismatch, implausible photo, or requested

Crop-specific advice is **withheld** (`advisory: null`) when the predicted crop is not the
selected crop, because recommending a tomato treatment for something the model called
potato would be harmful.

## `GET /api/weather`

`?latitude=&longitude=&disease=` → current conditions and a deterministic risk band.

```json
{
  "available": true,
  "source": "open-meteo",
  "weather": { "temperature_c": 29.2, "humidity_pct": 78.0, "rain_mm": 0.0, "wind_kmh": 28.6 },
  "risk": { "level": "Low", "score": 0, "max_score": 12, "factors": ["…"], "spray_window": "…" }
}
```

Risk is a documented 0–9 point table plus a disease adjustment, not a raw data readout.
Falls back to MET Norway when Open-Meteo rate-limits; `source` says which answered.

## `GET /api/outbreaks`

Public map data. `?crop_type=&disease=&risk=&region_name=&days=`

Coordinates are **rounded to 2 decimal places (~1 km)**; no farmer name, image, expert
notes or token is included. Identical location + crop + disease reports collapse into one
point with a count.

```json
{
  "points": [
    { "latitude": 16.51, "longitude": 80.65, "crop_type": "Tomato",
      "predicted_disease": "Tomato___Late_blight", "weather_risk_level": "Low",
      "status": "Pending_Expert_Review", "reports": 2, "latest_at": "2026-10-26T11:44:00+00:00" }
  ],
  "summary": { "total_reports": 2, "points": 2, "period_days": 90,
               "by_crop": { "Tomato": 2 }, "by_risk": { "Low": 2 },
               "by_disease": { "…": 2 }, "hotspots": [ { "…": "…" } ] },
  "attribution": "Weather data by Open-Meteo.com (CC BY 4.0)…"
}
```

## `GET /api/reports` — staff

The review queue. Contains every farmer's name and GPS, hence staff-only.
`?crop_type=&disease=&risk=&status=&region_name=&limit=&offset=`

## `GET /api/reports/{id}` and `GET /api/reports/by-reference/{reference}`

Owner (`X-Report-Token`) or staff. The reference form accepts `CG-00002`, `cg-2` or `2`.

## `PATCH /api/reports/{id}/verify` — expert

```json
{ "action": "confirm", "expert_name": "Dr Rao", "expert_notes": "Consistent with late blight" }
```

or `{ "action": "correct", "corrected_disease": "Tomato___Early_blight", … }`. The
corrected disease must belong to the report's crop. A second review of the same report
returns `409`. The AI prediction is preserved in `predicted_disease` regardless.

Every decision writes an `audit_events` row: actor role and name (from the token), report,
previous state, new state, notes, timestamp.

## `POST /api/reports/{id}/followup`

Owner or staff, `multipart/form-data`: `condition_status` (`Recovering`, `No_Change`,
`Worsening`), `farmer_notes`, optional `image`.

## `GET /api/stats` — staff

Aggregate counts by status, crop and risk for operational dashboards.
