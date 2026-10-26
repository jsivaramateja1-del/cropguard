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

## Accounts (added after the initial token-only model)

Farmers can now create an account; the anonymous access-code path still works and is not
deprecated.

| Concern | How it is handled |
|---|---|
| Password storage | bcrypt, cost 12. Never logged, never returned |
| Password policy | Minimum 10 characters, maximum 200 bytes (bcrypt truncates past 72) |
| Sessions | Signed JWT (HS256), 24-hour expiry, carrying user id, role and a `ver` claim |
| Revocation | Bumping `users.token_version` invalidates every token issued before it — used by logout, password change and account deletion |
| Signing key | `SECRET_KEY`. Unset means a random per-process key: safe, but signs everyone out on restart |
| Brute force | 8 failures per email and per client per 15 minutes, then `429` with `Retry-After` |
| Account enumeration | Registration and login return identical responses whether or not an address exists |
| Role elevation | Registration can only create a farmer. Expert and admin accounts are created by an admin via `POST /api/admin/users`. A `role` field in a registration body is ignored |
| Report ownership | `reports.owner_user_id` links a signed-in farmer's reports; anonymous reports stay reachable only by access code |
| Account deletion | Deactivates the account, anonymises the farmer name on its reports, keeps the aggregate for the outbreak map |

The legacy `EXPERT_TOKEN` shared secret still authenticates staff. It is kept so the
already-deployed expert flow keeps working, and it is documented as legacy rather than
recommended. A JWT for an expert or admin account is the supported path.

## Expert approval workflow

Recognition is requested, not granted:

1. A signed-in farmer posts to `POST /api/expert-requests` with a justification. The
   account **remains a farmer**; the request creates a `pending` row and nothing else.
2. An administrator lists the queue at `GET /api/expert-requests` (admin role only) and
   calls `.../approve` or `.../reject` with an optional note.
3. Approval sets `users.role = expert` **and bumps `token_version`**, so the token the
   applicant held while a farmer stops working. They sign in again and receive an expert
   token. Without that bump, an old farmer-scoped token would silently gain staff powers.
4. Both decisions write an `audit_events` row with the administrator's identity.

Guards: one pending request per applicant; staff cannot apply; a request cannot be
reviewed twice (`409`); only an administrator can approve, so nobody can approve
themselves; a rejected applicant may apply again.

`audit_events.report_id` is nullable so staff actions with no report — approving an
expert, creating an account — can be audited too.

## Known limitations — stated rather than hidden

1. **No password reset by email.** There is no mail provider configured, so a forgotten
   password means an admin resets it. Adding one needs an SMTP or Resend/SES account.
2. **The legacy shared staff token still works.** Kept deliberately so the deployed expert
   page does not break; it should be removed once every expert has an account.
3. **Rate limiting is per process.** With several workers the effective limit multiplies.
   A shared store (Redis) is the production answer.
4. **Images are served from `/uploads` without per-request authorisation.** Filenames are
   unguessable UUIDs, which is obscurity rather than access control. Configuring
   Cloudinary with signed URLs, or moving to authenticated image proxying, closes this.
5. **No TLS termination of our own** — Render and Vercel provide HTTPS in front.
6. **`GET /api/reports` returns farmer names to any staff token.** Acceptable for a
   review queue; a production deployment would add field-level redaction by role.

## Verifying it

```bash
cd backend
python -m pytest tests/test_security.py tests/test_auth.py -v
```

25 account tests (registration, role boundaries, throttling, expiry, revocation,
deletion) plus the access-control suite.
