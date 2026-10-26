# Security model

What protects what, why it is built this way, and what is deliberately not done yet.

## The problem this fixed

The first version had **no authentication on any route**. Anyone who found the URL could:

- `GET /api/reports` — read every farmer's name, GPS position and diagnosis
- `GET /api/reports/{id}` — read any report by walking sequential ids
- `PATCH /api/reports/{id}/verify` — claim to be an agricultural expert and change a diagnosis
- `POST /api/reports/{id}/followup` — append observations to anyone's report
- `GET /api/stats` — read operational aggregates

All five are now closed, with tests that pin them shut (`backend/tests/test_security.py`).

## Identity model

### Farmers are anonymous by design

A farmer photographing a leaf in a field should not have to create an account. Submitting
a photo returns a **one-time access token**:

- 32 bytes from `secrets.token_urlsafe`
- Only a SHA-256 hash is stored (`reports.owner_token_hash`)
- Returned exactly once, in the `access_token` field of the analysis response
- Sent back as the `X-Report-Token` header to read the report or add a follow-up

Report ids remain sequential and are shown as a public reference (`CG-00042`). That
reference identifies a report to a human and **grants nothing**. A wrong token and a
missing token produce an identical `404`, so the endpoint cannot be used to probe which
reports exist.

SHA-256 without a salt is the right choice for a 256-bit random token — there is no
dictionary to attack. It would be the wrong choice for a password, which is one reason
passwords are not used here.

### Experts and administrators authenticate with a token

`EXPERT_TOKEN` and `ADMIN_TOKEN` are configured in the environment and sent as
`Authorization: Bearer <token>`. Comparison is constant-time.

**There is no default value for either.** An unconfigured deployment therefore has no
privileged access at all, rather than a known default password.

The expert name submitted in a verification request is recorded as a note and **never
establishes authorisation** — the token does. The audit log records the identity bound to
the token, so an expert cannot choose what the history says about them.

## What is enforced, and where

| Endpoint | Farmer (owner token) | Expert / admin |
|---|---|---|
| `POST /api/analyze` | allowed, rate limited | allowed |
| `GET /api/reports` | denied | allowed |
| `GET /api/reports/{id}` | own report only | any |
| `PATCH /api/reports/{id}/verify` | denied | allowed |
| `POST /api/reports/{id}/followup` | own report only | any |
| `GET /api/stats` | denied | allowed |
| `GET /api/model` | allowed (public model metadata) | allowed |
| `GET /health` | allowed (no sensitive detail) | allowed |

Enforcement is server-side on every request. Hiding a button in the frontend is not an
access control and is not relied on anywhere.

## Audit trail

Every verification writes an `audit_events` row:

- actor role and name (from the token, not the request body)
- report id
- previous state (`Pending_Expert_Review:Tomato___Early_blight`)
- new state (`Expert_Confirmed:Tomato___Early_blight`)
- review notes
- timestamp

Rows are append-only: the application never updates or deletes them.

## Input and upload hardening

- Declared `Content-Type` is checked, but **the decoded format is what counts** — bytes
  that decode to a GIF or TIFF are rejected with `415` even if named `leaf.jpg`.
- Uploads are capped at 8 MB and read in chunks, so an oversized body is refused without
  being buffered whole.
- Pixel-count limits reject decompression bombs.
- Stored filenames are generated UUIDs; the client filename is never used as a path.
- `/api/analyze` is rate limited per client (default 20 per 60 s) and returns `429` with
  `Retry-After`.

## Known limitations — stated rather than hidden

1. **Shared secrets, not user accounts.** There is one expert token for all experts. There
   is no registration, no password reset, no per-user revocation, and no way to attribute
   two different experts acting under the same token. The upgrade path is an identity
   provider (Auth0, Supabase Auth, Cognito) issuing short-lived JWTs with per-user claims;
   the authorization checks in `security.py` are the only code that would change.
2. **Rate limiting is per process.** With several workers the effective limit multiplies.
   A shared store (Redis) is the production answer.
3. **Images are served from `/uploads` without per-request authorisation.** Filenames are
   unguessable UUIDs, which is obscurity rather than access control. Configuring
   Cloudinary with signed URLs, or moving to authenticated image proxying, closes this.
4. **No TLS termination of our own** — Render and Vercel provide HTTPS in front.
5. **`GET /api/reports` returns farmer names to any staff token.** Acceptable for a
   review queue; a production deployment would add field-level redaction by role.

## Verifying it

```bash
cd backend
python -m pytest tests/test_security.py -v
```

27 tests covering unauthenticated access, cross-owner access, bogus tokens, the expert
name not authorising, audit contents, unsupported crops, advisory suppression on crop
mismatch, health not leaking paths, and rate limiting.
