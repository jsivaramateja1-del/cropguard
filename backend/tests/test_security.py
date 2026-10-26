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
    for forbidden in ("/opt/", "/scratch", "/usr/", "uploads_dir", "sqlite:", "postgres"):
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
