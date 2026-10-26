"""Security and authorization tests.

These exist because the first version of CropGuard had **no authentication at all**: any
caller could list every report, read any farmer's name and GPS position, and call
`PATCH /verify` claiming to be an expert. Each test here pins one of those closed.

Run from backend/:
    python -m pytest tests/test_security.py -v
"""

from __future__ import annotations

import io
import json

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select

import database
import main
import models
import security

EXPERT = {"Authorization": "Bearer test-expert-token"}
ADMIN = {"Authorization": "Bearer test-admin-token"}


@pytest.fixture()
def client() -> TestClient:
    with TestClient(main.app) as test_client:
        yield test_client


@pytest.fixture()
def leaf() -> bytes:
    rng = np.random.default_rng(11)
    array = rng.integers(60, 180, (320, 320, 3), dtype=np.uint8)
    array[:, :, 1] = np.clip(array[:, :, 1] + 40, 0, 255)
    buffer = io.BytesIO()
    Image.fromarray(array).save(buffer, format="JPEG")
    return buffer.getvalue()


@pytest.fixture(autouse=True)
def _clean():
    with database.SessionLocal() as session:
        session.query(models.ReportImage).delete()
        session.query(models.FollowUp).delete()
        session.query(models.AuditEvent).delete()
        session.query(models.Report).delete()
        session.commit()
    yield


def _create(client: TestClient, leaf: bytes, **overrides) -> dict:
    data = {
        "crop_type": "Tomato",
        "farmer_name": "Ravi Kumar",
        "latitude": "16.5062",
        "longitude": "80.6480",
        "request_expert_review": "true",
    }
    data.update(overrides)
    response = client.post(
        "/api/analyze", files={"image": ("leaf.jpg", leaf, "image/jpeg")}, data=data
    )
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------------------
# Report access
# ---------------------------------------------------------------------------
def test_listing_reports_requires_staff(client):
    assert client.get("/api/reports").status_code == 401


def test_stats_requires_staff(client):
    assert client.get("/api/stats").status_code == 401


def test_report_is_not_readable_by_id_alone(client, leaf):
    created = _create(client, leaf)
    report_id = created["report"]["id"]
    # No token: must not be readable even though the id is known.
    assert client.get(f"/api/reports/{report_id}").status_code == 404


def test_wrong_token_looks_identical_to_missing(client, leaf):
    created = _create(client, leaf)
    report_id = created["report"]["id"]
    missing = client.get(f"/api/reports/{report_id}")
    wrong = client.get(f"/api/reports/{report_id}", headers={"X-Report-Token": "nope"})
    # Same status and same body: the response cannot be used to probe which ids exist.
    assert missing.status_code == wrong.status_code == 404
    assert missing.json() == wrong.json()


def test_owner_token_grants_access(client, leaf):
    created = _create(client, leaf)
    report_id = created["report"]["id"]
    token = created["access_token"]
    response = client.get(f"/api/reports/{report_id}", headers={"X-Report-Token": token})
    assert response.status_code == 200
    assert response.json()["id"] == report_id


def test_staff_can_read_any_report(client, leaf):
    created = _create(client, leaf)
    response = client.get(f"/api/reports/{created['report']['id']}", headers=EXPERT)
    assert response.status_code == 200


def test_access_token_is_returned_once_and_stored_hashed(client, leaf):
    created = _create(client, leaf)
    token = created["access_token"]
    assert token and len(token) > 30

    report_id = created["report"]["id"]
    with database.SessionLocal() as session:
        stored = session.get(models.Report, report_id)
        assert stored.owner_token_hash != token
        assert stored.owner_token_hash == security.hash_token(token)
        # The raw token must never come back on a later read.
        assert token not in json.dumps(session.get(models.Report, report_id).reference or "")


def test_report_reference_is_public_but_grants_nothing(client, leaf):
    created = _create(client, leaf)
    assert created["report"]["reference"] == f"CG-{created['report']['id']:05d}"
    # Knowing the reference does not open the report.
    assert client.get(f"/api/reports/{created['report']['id']}").status_code == 404


# ---------------------------------------------------------------------------
# Expert verification
# ---------------------------------------------------------------------------
def test_verification_requires_a_token(client, leaf):
    created = _create(client, leaf)
    response = client.patch(
        f"/api/reports/{created['report']['id']}/verify",
        json={"action": "confirm", "expert_name": "Dr Rao"},
    )
    assert response.status_code == 401


def test_verification_rejects_a_bogus_token(client, leaf):
    created = _create(client, leaf)
    response = client.patch(
        f"/api/reports/{created['report']['id']}/verify",
        json={"action": "confirm", "expert_name": "Dr Rao"},
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert response.status_code == 401


def test_expert_name_in_body_does_not_authorise(client, leaf):
    """Supplying an expert-looking name must not substitute for credentials."""
    created = _create(client, leaf)
    response = client.patch(
        f"/api/reports/{created['report']['id']}/verify",
        json={"action": "confirm", "expert_name": "Dr Chief Pathologist"},
        headers={"X-Report-Token": created["access_token"]},
    )
    assert response.status_code == 401


def test_expert_can_verify_and_audit_records_the_token_identity(client, leaf):
    created = _create(client, leaf)
    report_id = created["report"]["id"]

    response = client.patch(
        f"/api/reports/{report_id}/verify",
        json={"action": "confirm", "expert_name": "Dr Rao", "expert_notes": "Looks right"},
        headers=EXPERT,
    )
    assert response.status_code == 200
    assert response.json()["status"] == "Expert_Confirmed"

    with database.SessionLocal() as session:
        events = session.execute(select(models.AuditEvent)).scalars().all()
        assert len(events) == 1
        event = events[0]
        # Identity comes from the configured token, not from the request body.
        assert event.actor_role == "expert"
        assert event.actor_name == "Dr Test Expert"
        assert event.action == "verify"
        assert event.previous_state.startswith("Pending_Expert_Review")
        assert "Expert_Confirmed" in event.new_state
        assert event.notes == "Looks right"


def test_admin_token_also_verifies(client, leaf):
    created = _create(client, leaf)
    response = client.patch(
        f"/api/reports/{created['report']['id']}/verify",
        json={"action": "confirm", "expert_name": "Admin"},
        headers=ADMIN,
    )
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Follow-ups
# ---------------------------------------------------------------------------
def test_follow_up_requires_the_owner_token(client, leaf):
    created = _create(client, leaf)
    response = client.post(
        f"/api/reports/{created['report']['id']}/followup",
        data={"condition_status": "Recovering", "farmer_notes": "Better"},
    )
    assert response.status_code == 404


def test_owner_can_add_a_follow_up(client, leaf):
    created = _create(client, leaf)
    response = client.post(
        f"/api/reports/{created['report']['id']}/followup",
        data={"condition_status": "Recovering", "farmer_notes": "Better"},
        headers={"X-Report-Token": created["access_token"]},
    )
    assert response.status_code == 201


# ---------------------------------------------------------------------------
# Model capability, not frontend claims
# ---------------------------------------------------------------------------
def test_wheat_is_refused_because_the_model_has_no_wheat_classes(client, leaf):
    response = client.post(
        "/api/analyze",
        files={"image": ("leaf.jpg", leaf, "image/jpeg")},
        data={
            "crop_type": "Wheat",
            "farmer_name": "Ravi Kumar",
            "latitude": "16.5",
            "longitude": "80.6",
        },
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "Wheat is not supported by the deployed model" in detail
    assert "Tomato" in detail  # tells the caller what does work


def test_crop_mismatch_withholds_crop_specific_advice(client, leaf):
    """The test model predicts a Tomato class; selecting Rice must not yield rice advice."""
    response = client.post(
        "/api/analyze",
        files={"image": ("leaf.jpg", leaf, "image/jpeg")},
        data={
            "crop_type": "Rice",
            "farmer_name": "Ravi Kumar",
            "latitude": "16.5",
            "longitude": "80.6",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["report"]["predicted_disease"].startswith("Tomato")
    assert payload["advisory"] is None, "advice for another crop must be withheld"
    assert any("looks like Tomato" in warning for warning in payload["warnings"])


def test_model_contract_reports_real_capability(client):
    payload = client.get("/api/model").json()
    assert payload["output_dimensions"] == len(payload["classes"])
    assert "Wheat" not in payload["supported_crops"]
    # The deployed model has no healthy-rice class; the contract must say so.
    assert payload["healthy_class_available"].get("Rice") is False
    assert payload["normalization"]["mean"] == [0.485, 0.456, 0.406]


# ---------------------------------------------------------------------------
# Health must not leak operational detail
# ---------------------------------------------------------------------------
def test_health_exposes_no_paths_or_credentials(client):
    body = client.get("/health").text
    for forbidden in (
        "/opt/", "/scratch", "/usr/", "uploads_dir",
        "sqlite:", "postgresql://", "postgres://", "@db.", "password",
    ):
        assert forbidden not in body, f"health response leaks {forbidden}"
    assert "token" not in body.lower() or "staff_configured" in body


def test_health_is_unhealthy_when_the_model_is_missing(client, monkeypatch):
    """An API that cannot diagnose anything must not report itself healthy."""
    monkeypatch.setattr(
        main.get_model_service(),
        "describe",
        lambda: {
            "available": False, "backend": None, "artifact_type": "unknown",
            "model_version": "unknown", "classes": 0, "image_size": 224,
            "onnx_present": False, "torch_present": False, "load_seconds": 0.0,
            "error": "no artifact",
        },
    )
    payload = client.get("/health").json()
    assert payload["status"] == "unhealthy"
    assert payload["checks"]["model"]["ok"] is False


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------
def test_analysis_is_rate_limited(client, leaf, monkeypatch):
    limiter = security.SlidingWindowLimiter(limit=2, window_seconds=60)
    monkeypatch.setattr(security, "_analyze_limiter", limiter)

    statuses = []
    for _ in range(3):
        response = client.post(
            "/api/analyze",
            files={"image": ("leaf.jpg", leaf, "image/jpeg")},
            data={
                "crop_type": "Tomato",
                "farmer_name": "Ravi Kumar",
                "latitude": "16.5",
                "longitude": "80.6",
            },
        )
        statuses.append(response.status_code)

    assert statuses[:2] == [200, 200]
    assert statuses[2] == 429
    assert "Retry-After" in response.headers


# ---------------------------------------------------------------------------
# Schema migration
# ---------------------------------------------------------------------------
def test_additive_migration_upgrades_an_older_database(tmp_path):
    """A database created before the security change must still work after it.

    `create_all` creates missing tables but never alters an existing one, so without the
    additive migration a live SQLite deployment would reject every insert.
    """
    import sqlite3

    from sqlalchemy import create_engine, inspect, text

    db_path = tmp_path / "legacy.db"
    connection = sqlite3.connect(db_path)
    connection.execute(
        """CREATE TABLE reports (
               id INTEGER PRIMARY KEY, farmer_name VARCHAR(120) NOT NULL,
               crop_type VARCHAR(32) NOT NULL, image_url VARCHAR(512) NOT NULL,
               predicted_disease VARCHAR(120) NOT NULL, confidence FLOAT NOT NULL,
               latitude FLOAT NOT NULL, longitude FLOAT NOT NULL,
               status VARCHAR(32) NOT NULL, created_at TIMESTAMP NOT NULL)"""
    )
    connection.commit()
    connection.close()


    import database

    original_engine = database.engine
    original_url = database.DATABASE_URL
    try:
        database.engine = create_engine(f"sqlite:///{db_path}")
        database.DATABASE_URL = f"sqlite:///{db_path}"
        database._apply_additive_migrations()
        columns = {c["name"] for c in inspect(database.engine).get_columns("reports")}
        assert "owner_token_hash" in columns
        assert "reference" in columns
        with database.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO reports (farmer_name, crop_type, image_url, "
                    "predicted_disease, confidence, latitude, longitude, status, "
                    "created_at, owner_token_hash, reference) VALUES "
                    "('a','Tomato','u','Tomato___healthy',0.9,1,2,'AI_Verified',"
                    "'2026-01-01',NULL,'CG-00001')"
                )
            )
    finally:
        database.engine = original_engine
        database.DATABASE_URL = original_url


# ---------------------------------------------------------------------------
# Public outbreak map
# ---------------------------------------------------------------------------
def test_outbreaks_are_public_but_anonymised(client, leaf):
    """The map is public; it must not leak who reported what."""
    _create(client, leaf)
    _create(client, leaf)

    response = client.get("/api/outbreaks")
    assert response.status_code == 200, "the map must not require a staff token"
    body = response.json()

    assert body["summary"]["total_reports"] == 2
    # Identical location/crop/disease collapses into one point with a count.
    assert body["summary"]["points"] == 1
    assert body["points"][0]["reports"] == 2

    text = response.text
    for forbidden in ("Ravi Kumar", "farmer_name", "owner_token", "access_token",
                      "/uploads/", "expert_notes", "image_url"):
        assert forbidden not in text, f"public map leaks {forbidden}"


def test_outbreak_coordinates_are_rounded(client, leaf):
    _create(client, leaf, latitude="16.50621", longitude="80.64809")
    point = client.get("/api/outbreaks").json()["points"][0]
    # ~1 km grid: a farm must not be pinpointed from a public endpoint.
    assert point["latitude"] == 16.51
    assert point["longitude"] == 80.65


def test_outbreaks_summary_and_filters(client, leaf):
    _create(client, leaf, crop_type="Tomato")
    _create(client, leaf, crop_type="Rice")

    payload = client.get("/api/outbreaks").json()
    assert payload["summary"]["by_crop"] == {"Tomato": 1, "Rice": 1}
    assert len(payload["summary"]["hotspots"]) >= 1
    assert payload["summary"]["period_days"] == 90

    filtered = client.get("/api/outbreaks", params={"crop_type": "Rice"}).json()
    assert filtered["summary"]["total_reports"] == 1
    assert filtered["points"][0]["crop_type"] == "Rice"


def test_outbreaks_empty_database_is_valid(client):
    payload = client.get("/api/outbreaks").json()
    assert payload["points"] == []
    assert payload["summary"]["total_reports"] == 0


# ---------------------------------------------------------------------------
# Startup resilience
# ---------------------------------------------------------------------------
def test_api_starts_and_reports_an_unreachable_database(monkeypatch):
    """An unreachable database must not take the whole service down.

    This is the Render failure that produced "Exited with status 3": init_db() raised
    during startup and uvicorn aborted. The API should start, say the database is down,
    and keep serving everything that does not need storage.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import database

    bad_engine = create_engine("postgresql://u:p@127.0.0.1:1/none")
    original_engine = database.engine
    original_session = database.SessionLocal
    monkeypatch.setattr(database, "engine", bad_engine)
    # The session factory is bound at import time, so it has to be redirected too for the
    # health check to see the same failure a real deployment would.
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=bad_engine, autoflush=False))
    monkeypatch.setattr(database, "INIT_ERROR", None)
    try:
        error = database.try_init_db()
        assert error is not None, "an unreachable database must be reported, not raised"
        assert "OperationalError" in error or "Error" in error

        with TestClient(main.app) as client:
            health = client.get("/health")
            assert health.status_code == 200
            body = health.json()
            assert body["status"] == "unhealthy"
            assert body["checks"]["database"]["ok"] is False
            assert body["checks"]["database"]["initialised"] is False
            # Storage-free endpoints still work.
            assert client.get("/api/model").status_code == 200
            # Storage-dependent ones fail honestly rather than with a traceback.
            assert client.get("/api/outbreaks").status_code == 503
    finally:
        database.engine = original_engine
        database.SessionLocal = original_session
        database.INIT_ERROR = None


# ---------------------------------------------------------------------------
# Database host diagnostics
# ---------------------------------------------------------------------------
def test_host_diagnostics_identifies_an_ipv6_only_host(monkeypatch):
    """Supabase's direct host is AAAA-only; Render is IPv4-only. That must be visible."""
    import socket as socket_module

    from sqlalchemy import create_engine

    import database

    original = database.engine
    monkeypatch.setattr(
        database, "engine",
        create_engine("postgresql://u:p@db.example.supabase.co:5432/postgres"),
    )
    try:
        monkeypatch.setattr(
            socket_module, "getaddrinfo",
            lambda *a, **k: [(socket_module.AF_INET6, 1, 6, "", ("2406:da1a::1", 0, 0, 0))],
        )
        result = database.host_diagnostics()
        assert result == {"applies": True, "resolves": True, "address_family": "ipv6_only"}

        monkeypatch.setattr(
            socket_module, "getaddrinfo",
            lambda *a, **k: [(socket_module.AF_INET, 1, 6, "", ("1.2.3.4", 0))],
        )
        assert database.host_diagnostics()["address_family"] == "ipv4"

        def _fail(*_a, **_k):
            raise socket_module.gaierror("nope")

        monkeypatch.setattr(socket_module, "getaddrinfo", _fail)
        assert database.host_diagnostics() == {
            "applies": True, "resolves": False, "address_family": "none"
        }
    finally:
        database.engine = original


def test_host_diagnostics_is_skipped_for_sqlite():
    import database

    assert database.host_diagnostics() == {"applies": False}


# ---------------------------------------------------------------------------
# Malformed DATABASE_URL detection
# ---------------------------------------------------------------------------
def test_unencoded_at_in_password_is_diagnosed(monkeypatch):
    """An '@' in the password shifts the username/host boundary and breaks DNS."""
    from sqlalchemy import create_engine

    import database

    original_engine, original_url = database.engine, database.DATABASE_URL
    bad = (
        "postgresql://postgres.abc:Learn@Sc!ds543$"
        "aws-0-ap-south-1.pooler.supabase.com:5432/postgres"
    )
    monkeypatch.setattr(database, "engine", create_engine(bad))
    monkeypatch.setattr(database, "DATABASE_URL", bad)
    try:
        result = database.credential_diagnostics()
        assert result["problem"] == "password_not_url_encoded"
        assert "%40" in result["hint"]
        # And it short-circuits startup with that reason rather than a DNS error.
        monkeypatch.setattr(database, "INIT_ERROR", None)
        monkeypatch.setattr(database, "INIT_REASON", None)
        assert database.try_init_db() is not None
        assert database.INIT_REASON == "password_not_url_encoded"
    finally:
        database.engine, database.DATABASE_URL = original_engine, original_url
        database.INIT_ERROR = None
        database.INIT_REASON = None


def test_encoded_password_is_accepted(monkeypatch):
    from sqlalchemy import create_engine

    import database

    good = (
        "postgresql://postgres.abc:Learn%40Sc%21ds543%24"
        "@aws-0-ap-south-1.pooler.supabase.com:5432/postgres"
    )
    original_engine, original_url = database.engine, database.DATABASE_URL
    monkeypatch.setattr(database, "engine", create_engine(good))
    monkeypatch.setattr(database, "DATABASE_URL", good)
    try:
        assert database.credential_diagnostics()["problem"] is None
    finally:
        database.engine, database.DATABASE_URL = original_engine, original_url


def test_unreplaced_placeholder_is_diagnosed(monkeypatch):
    from sqlalchemy import create_engine

    import database

    bad = "postgresql://postgres.abc:[YOUR-PASSWORD]@aws-0-ap-south-1.pooler.supabase.com:5432/postgres"
    original_engine, original_url = database.engine, database.DATABASE_URL
    monkeypatch.setattr(database, "engine", create_engine(bad))
    monkeypatch.setattr(database, "DATABASE_URL", bad)
    try:
        assert database.credential_diagnostics()["problem"] == "placeholder_not_replaced"
    finally:
        database.engine, database.DATABASE_URL = original_engine, original_url


# ---------------------------------------------------------------------------
# Lookup by public reference (the follow-up page path)
# ---------------------------------------------------------------------------
def test_reference_lookup_needs_the_access_code(client, leaf):
    created = _create(client, leaf)
    reference = created["report"]["reference"]

    assert client.get(f"/api/reports/by-reference/{reference}").status_code == 404

    response = client.get(
        f"/api/reports/by-reference/{reference}",
        headers={"X-Report-Token": created["access_token"]},
    )
    assert response.status_code == 200
    assert response.json()["id"] == created["report"]["id"]


def test_reference_lookup_accepts_a_bare_number_and_any_case(client, leaf):
    created = _create(client, leaf)
    reference = created["report"]["reference"]  # CG-00001
    token = {"X-Report-Token": created["access_token"]}

    for variant in (reference.lower(), reference.replace("CG-", ""), f"  {reference}  "):
        response = client.get(f"/api/reports/by-reference/{variant.strip()}", headers=token)
        assert response.status_code == 200, variant


def test_reference_lookup_unknown_reference(client):
    response = client.get("/api/reports/by-reference/CG-99999", headers=EXPERT)
    assert response.status_code == 404
    assert "No report with that reference" in response.json()["detail"]


# ---------------------------------------------------------------------------
# Durable image storage
# ---------------------------------------------------------------------------
def test_uploaded_image_is_served_from_the_database(client, leaf):
    """Images live in the database, so they survive a redeploy."""
    created = _create(client, leaf)
    url = created["report"]["image_url"]
    assert url.startswith("/api/images/")

    fetched = client.get(url)
    assert fetched.status_code == 200
    assert fetched.headers["content-type"].startswith("image/")
    assert len(fetched.content) == len(leaf)
    assert fetched.headers["etag"]
    assert "immutable" in fetched.headers["cache-control"]

    # The bytes really are in the database, not on disk.
    token = url.rsplit("/", 1)[-1]
    with database.SessionLocal() as session:
        row = session.execute(
            select(models.ReportImage).where(models.ReportImage.token == token)
        ).scalar_one()
        assert row.data == leaf
        assert row.size_bytes == len(leaf)


def test_unknown_image_token_is_a_404(client):
    assert client.get("/api/images/not-a-real-token").status_code == 404


def test_image_tokens_are_unguessable(client, leaf):
    """Two uploads of identical bytes get different tokens."""
    first = _create(client, leaf)["report"]["image_url"]
    second = _create(client, leaf)["report"]["image_url"]
    assert first != second


def test_health_reports_which_build_is_live(client):
    """Without this there is no way to tell whether a deploy picked up the latest code."""
    body = client.get("/health").json()
    assert "build" in body
    assert body["build"]["commit"]
    assert body["build"]["environment"] in {"local", "render", "vercel"}
    # A commit SHA is public; the build block must not smuggle anything else in.
    assert set(body["build"]) == {"commit", "environment"}


def test_report_risk_filter_works(client, leaf):
    """Ruff caught a NameError here: the risk filter crashed with a 500.

    Nothing exercised it before, because the other filter tests only used crop_type.
    """
    _create(client, leaf)
    for level in ("Low", "Moderate", "High", "Critical"):
        response = client.get("/api/reports", params={"risk": level}, headers=EXPERT)
        assert response.status_code == 200, f"{level}: {response.text}"

    invalid = client.get("/api/reports", params={"risk": "Extreme"}, headers=EXPERT)
    assert invalid.status_code == 422
    assert "Unknown risk level" in invalid.json()["detail"]


# ---------------------------------------------------------------------------
# Image retention
# ---------------------------------------------------------------------------
def test_retention_deletes_old_images_but_keeps_reports(client, leaf):
    """The report survives; only the bytes go, and image_url is cleared."""
    from datetime import datetime, timedelta, timezone

    import maintenance

    created = _create(client, leaf)
    report_id = created["report"]["id"]
    token = created["report"]["image_url"].rsplit("/", 1)[-1]

    with database.SessionLocal() as session:
        image = session.execute(
            select(models.ReportImage).where(models.ReportImage.token == token)
        ).scalar_one()
        image.created_at = datetime.now(timezone.utc) - timedelta(days=200)
        session.commit()

    with database.SessionLocal() as session:
        result = maintenance.purge_expired_images(session, days=90)
        assert result.deleted_images == 1
        assert result.freed_bytes > 0

    with database.SessionLocal() as session:
        assert session.get(models.Report, report_id) is not None, "the report must survive"
        report = session.get(models.Report, report_id)
        assert report.image_url is None, "the UI needs a null url to show its placeholder"
        assert session.execute(select(models.ReportImage)).scalars().all() == []


def test_retention_keeps_recent_images(client, leaf):
    import maintenance

    created = _create(client, leaf)
    with database.SessionLocal() as session:
        result = maintenance.purge_expired_images(session, days=90)
        assert result.deleted_images == 0

    url = created["report"]["image_url"]
    assert client.get(url).status_code == 200


def test_retention_enforces_the_size_cap(client, leaf, monkeypatch):
    import maintenance
    from config import settings

    monkeypatch.setattr(settings, "image_max_total_mb", 0, raising=False)
    _create(client, leaf)

    with database.SessionLocal() as session:
        result = maintenance.purge_expired_images(session)
        assert result.reason == "size cap"
        assert result.deleted_images >= 1
        assert result.remaining_bytes == 0


def test_retention_endpoint_is_admin_only(client):
    assert client.post("/api/admin/maintenance/retention").status_code == 401

    # A signed-in farmer is authenticated but not permitted.
    farmer = client.post(
        "/api/auth/register",
        json={"email": "farmer@example.com", "password": "a-long-password-1", "display_name": "Farmer"},
    ).json()["access_token"]
    assert client.post(
        "/api/admin/maintenance/retention", headers={"Authorization": f"Bearer {farmer}"}
    ).status_code == 403

    assert client.post("/api/admin/maintenance/retention", headers=ADMIN).status_code == 200


def test_health_reports_image_usage(client, leaf):
    _create(client, leaf)
    usage = client.get("/health").json()["checks"]["images"]
    assert usage["images"] >= 1
    assert usage["retention_days"] == 90
    assert usage["max_total_mb"] > 0


def test_diseases_endpoint_is_public_and_marks_model_coverage(client):
    """The grid must not imply every advisory entry can be diagnosed from a photo."""
    response = client.get("/api/diseases")
    assert response.status_code == 200
    body = response.json()

    assert body["total"] == 21
    assert "Tomato" in body["crops"]
    assert body["model_classes"] == len(main.get_model_service().class_names)

    supported = [card for card in body["diseases"] if card["supported_by_model"]]
    unsupported = [card for card in body["diseases"] if not card["supported_by_model"]]
    assert supported, "the fixture model recognises some classes"
    assert unsupported, "wheat entries are in the advisory DB but not in the model"
    assert all(card["crop"] != "Wheat" for card in supported)

    # Cards carry real advisory content, not placeholders.
    late_blight = next(
        card for card in body["diseases"] if card["label"] == "Tomato___Late_blight"
    )
    assert late_blight["symptoms"]
    assert late_blight["summary"]
    assert late_blight["is_healthy"] is False
