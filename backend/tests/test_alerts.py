"""Outbreak alert tests.

The rules worth pinning: consent before delivery, a distance that is actually respected,
a cooldown that prevents flooding, and an unsubscribe that works with one click.
"""

from __future__ import annotations

import io
import re

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

import alerts
import database
import models
from config import settings
from main import app


@pytest.fixture()
def client() -> TestClient:
    with TestClient(app) as test_client:
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
def _reset_throttles():
    """The throttle is per-process, so it would otherwise leak between tests."""
    import auth
    import security

    auth.login_throttle().reset_all()
    security.analyze_limiter().reset()
    yield
    auth.login_throttle().reset_all()


@pytest.fixture(autouse=True)
def _clean():
    with database.SessionLocal() as session:
        session.query(models.AlertSubscription).delete()
        session.query(models.ReportImage).delete()
        session.query(models.FollowUp).delete()
        session.query(models.Report).delete()
        session.commit()
    yield


@pytest.fixture(autouse=True)
def _email_on(monkeypatch):
    monkeypatch.setattr(settings, "brevo_api_key", "test-key", raising=False)
    monkeypatch.setattr(settings, "email_from", "alerts@cropguard.test", raising=False)
    monkeypatch.setattr(settings, "public_app_url", "https://cropguard.test", raising=False)
    monkeypatch.setattr(settings, "alerts_enabled", True, raising=False)


def _capture(monkeypatch):
    sent = []

    class Result:
        delivered = True
        detail = "accepted"

    def fake_send(to_address, subject, html, text):
        sent.append({"to": to_address, "subject": subject, "html": html, "text": text})
        return Result()

    import emailer

    monkeypatch.setattr(emailer, "send_email", fake_send)
    return sent


def _subscribe(client, email="neighbour@example.com", lat=12.79, lon=80.20, **extra):
    payload = {"email": email, "latitude": lat, "longitude": lon}
    payload.update(extra)
    return client.post("/api/alerts/subscribe", json=payload)


def _confirm_token(sent, index=-1) -> str:
    match = re.search(r"token=([A-Za-z0-9_\-]+)", sent[index]["text"])
    assert match, "the confirmation email should contain a token"
    return match.group(1)


def _analyse(client, leaf, lat=12.7913, lon=80.2011, crop="Tomato"):
    return client.post(
        "/api/analyze",
        files={"image": ("leaf.jpg", leaf, "image/jpeg")},
        data={
            "crop_type": crop,
            "farmer_name": "Ravi Kumar",
            "latitude": str(lat),
            "longitude": str(lon),
        },
    )


# --- consent -----------------------------------------------------------------
def test_subscribing_sends_a_confirmation_and_not_an_alert(client, monkeypatch):
    sent = _capture(monkeypatch)
    response = _subscribe(client)
    assert response.status_code == 202
    assert len(sent) == 1
    assert "Confirm" in sent[0]["subject"]

    with database.SessionLocal() as session:
        row = session.query(models.AlertSubscription).one()
        assert row.confirmed_at is None, "nothing may be active before confirmation"


def test_confirmation_activates_the_subscription(client, monkeypatch):
    sent = _capture(monkeypatch)
    _subscribe(client)
    token = _confirm_token(sent)

    response = client.get("/api/alerts/confirm", params={"token": token})
    assert response.status_code == 200
    assert response.json()["status"] == "confirmed"

    with database.SessionLocal() as session:
        assert session.query(models.AlertSubscription).one().confirmed_at is not None

    # A second click is refused rather than silently re-confirming.
    assert client.get("/api/alerts/confirm", params={"token": token}).status_code == 400


def test_subscription_is_refused_when_email_is_not_configured(client, monkeypatch):
    monkeypatch.setattr(settings, "brevo_api_key", "", raising=False)
    response = _subscribe(client)
    assert response.status_code == 503
    assert "not configured" in response.json()["detail"]


# --- delivery ----------------------------------------------------------------
def test_a_nearby_report_alerts_a_confirmed_subscriber(client, leaf, monkeypatch):
    sent = _capture(monkeypatch)
    _subscribe(client, radius_km=10)
    client.get("/api/alerts/confirm", params={"token": _confirm_token(sent)})
    sent.clear()

    _analyse(client, leaf)

    assert len(sent) == 1
    assert "reported near you" in sent[0]["subject"]
    assert "Tomato" in sent[0]["subject"]
    assert "unsubscribe" in sent[0]["text"].lower()
    # The alert must not read like a prescription.
    assert "not a prescription" in sent[0]["text"].lower() or "Do not spray" in sent[0]["html"]


def test_a_distant_report_does_not_alert(client, leaf, monkeypatch):
    sent = _capture(monkeypatch)
    # Subscriber 40 km away with a 10 km radius.
    _subscribe(client, lat=13.15, lon=80.20, radius_km=10)
    client.get("/api/alerts/confirm", params={"token": _confirm_token(sent)})
    sent.clear()

    _analyse(client, leaf, lat=12.7913, lon=80.2011)
    assert sent == []


def test_an_unconfirmed_subscriber_is_never_emailed(client, leaf, monkeypatch):
    sent = _capture(monkeypatch)
    _subscribe(client)
    sent.clear()
    _analyse(client, leaf)
    assert sent == []


def test_crop_filter_is_respected(client, leaf, monkeypatch):
    sent = _capture(monkeypatch)
    _subscribe(client, crops=["Rice"])
    client.get("/api/alerts/confirm", params={"token": _confirm_token(sent)})
    sent.clear()

    _analyse(client, leaf, crop="Tomato")
    assert sent == [], "the subscriber only asked about rice"


def test_healthy_results_do_not_trigger_alerts(client, leaf, monkeypatch):
    sent = _capture(monkeypatch)
    _subscribe(client)
    client.get("/api/alerts/confirm", params={"token": _confirm_token(sent)})
    sent.clear()

    with database.SessionLocal() as session:
        report = models.Report(
            farmer_name="Ravi",
            crop_type="Tomato",
            image_url=None,
            predicted_disease="Tomato___healthy",
            confidence=0.95,
            latitude=12.7913,
            longitude=80.2011,
            status=models.ReportStatus.AI_VERIFIED,
        )
        session.add(report)
        session.commit()
        outcome = alerts.notify_neighbours(session, report)

    assert outcome.sent == 0
    assert sent == []


# --- flood control -----------------------------------------------------------
def test_the_cooldown_stops_a_second_alert(client, leaf, monkeypatch):
    sent = _capture(monkeypatch)
    _subscribe(client)
    client.get("/api/alerts/confirm", params={"token": _confirm_token(sent)})
    sent.clear()

    _analyse(client, leaf)
    assert len(sent) == 1

    _analyse(client, leaf)
    assert len(sent) == 1, "the cooldown should suppress the second alert"


def test_duplicate_addresses_get_one_email(client, leaf, monkeypatch):
    sent = _capture(monkeypatch)
    for _ in range(3):
        _subscribe(client, email="same@example.com")
    for index in range(3):
        client.get("/api/alerts/confirm", params={"token": _confirm_token(sent, index)})
    sent.clear()

    _analyse(client, leaf)
    assert len(sent) == 1


# --- unsubscribing -----------------------------------------------------------
def test_unsubscribe_removes_the_subscription(client, leaf, monkeypatch):
    sent = _capture(monkeypatch)
    _subscribe(client)
    client.get("/api/alerts/confirm", params={"token": _confirm_token(sent)})

    with database.SessionLocal() as session:
        token = session.query(models.AlertSubscription).one().unsubscribe_token

    assert client.post("/api/alerts/unsubscribe", params={"token": token}).status_code == 200
    with database.SessionLocal() as session:
        assert session.query(models.AlertSubscription).count() == 0

    sent.clear()
    _analyse(client, leaf)
    assert sent == []


def test_radius_is_capped(client, monkeypatch):
    _capture(monkeypatch)
    response = _subscribe(client, radius_km=500)
    # Rejected by validation rather than silently clamped to the country.
    assert response.status_code == 422


# --- distance maths ----------------------------------------------------------
def test_haversine_is_sane():
    # Chennai to Bengaluru is about 290 km.
    assert 280 < alerts.haversine_km(13.0827, 80.2707, 12.9716, 77.5946) < 300
    assert alerts.haversine_km(12.79, 80.20, 12.79, 80.20) == 0
