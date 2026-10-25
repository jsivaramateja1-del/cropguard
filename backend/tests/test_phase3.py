"""Phase 3 verification: inference, weather risk and the HTTP API.

Run from backend/:
    python -m pytest tests -v

These tests need neither torch nor network access: inference runs against a synthetic
ONNX graph generated in conftest, and the weather fetch is stubbed with an httpx
transport. What they prove is the plumbing and the contract, not model accuracy.
"""

from __future__ import annotations

import io
import json
from datetime import datetime, timezone
from typing import Optional

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

import advisory
import database
import inference
import main
import models
import storage
import weather_risk
from inference import InvalidImage, ModelService, ModelUnavailable, open_image, preprocess_image
from weather_risk import WeatherSnapshot, compute_risk


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session", autouse=True)
def _create_schema():
    database.init_db()
    yield


@pytest.fixture()
def client() -> TestClient:
    with TestClient(main.app) as test_client:
        yield test_client


@pytest.fixture()
def leaf_jpeg() -> bytes:
    """A real 320x320 JPEG with leaf-ish colours."""
    rng = np.random.default_rng(7)
    array = rng.integers(60, 180, (320, 320, 3), dtype=np.uint8)
    array[:, :, 1] = np.clip(array[:, :, 1] + 40, 0, 255)
    buffer = io.BytesIO()
    Image.fromarray(array).save(buffer, format="JPEG")
    return buffer.getvalue()


@pytest.fixture(autouse=True)
def _clean_reports():
    """Each test starts from an empty reports table."""
    with database.SessionLocal() as session:
        session.query(models.FollowUp).delete()
        session.query(models.Report).delete()
        session.commit()
    yield


def _analyze(client: TestClient, leaf_jpeg: bytes, **overrides):
    data = {
        "crop_type": "Tomato",
        "farmer_name": "Ravi Kumar",
        "latitude": "16.5062",
        "longitude": "80.6480",
        "region_name": "Vijayawada",
    }
    data.update({k: str(v) for k, v in overrides.items()})
    return client.post(
        "/api/analyze",
        files={"image": ("leaf.jpg", leaf_jpeg, "image/jpeg")},
        data=data,
    )


# ---------------------------------------------------------------------------
# Preprocessing and image validation
# ---------------------------------------------------------------------------
def test_preprocess_shape_and_normalisation():
    image = Image.new("RGB", (800, 600), (128, 128, 128))
    batch = preprocess_image(image)
    assert batch.shape == (1, 3, 224, 224)
    assert batch.dtype == np.float32
    # A mid-grey pixel normalised with ImageNet statistics lands near zero.
    assert abs(float(batch.mean())) < 0.35


def test_preprocess_handles_non_square_and_small_images():
    for size in ((120, 400), (1000, 1000), (97, 97)):
        batch = preprocess_image(Image.new("RGB", size, (90, 140, 90)))
        assert batch.shape == (1, 3, 224, 224)


def test_open_image_rejects_garbage():
    with pytest.raises(InvalidImage):
        open_image(b"this is not an image")


def test_open_image_rejects_tiny_images():
    buffer = io.BytesIO()
    Image.new("RGB", (40, 40), (0, 0, 0)).save(buffer, format="PNG")
    with pytest.raises(InvalidImage) as error:
        open_image(buffer.getvalue())
    assert "too small" in str(error.value)


def test_open_image_rejects_empty_bytes():
    with pytest.raises(InvalidImage):
        open_image(b"")


# ---------------------------------------------------------------------------
# Inference service
# ---------------------------------------------------------------------------
def test_model_service_loads_onnx_and_predicts():
    service = ModelService()
    service.load()
    assert service.available
    assert service.describe()["backend"] == "onnx"

    result = service.predict(Image.new("RGB", (400, 400), (120, 150, 100)), top_k=3)
    assert len(result.predictions) == 3
    assert result.top.label == "Tomato___Early_blight"
    assert result.top.crop == "Tomato"
    assert result.confidence > 0.75
    total = sum(p.probability for p in result.predictions)
    assert 0 < total <= 1.0
    assert result.artifact_type == "trained"
    assert result.inference_ms >= 0


def test_probabilities_are_sorted_descending():
    service = ModelService()
    result = service.predict(Image.new("RGB", (300, 300), (10, 200, 10)), top_k=4)
    probabilities = [p.probability for p in result.predictions]
    assert probabilities == sorted(probabilities, reverse=True)


def test_missing_model_reports_a_clear_error(tmp_path):
    service = ModelService(
        onnx_path=tmp_path / "nope.onnx",
        torch_path=tmp_path / "nope.pth",
        class_names_path=tmp_path / "nope.json",
    )
    with pytest.raises(ModelUnavailable) as error:
        service.load()
    message = str(error.value)
    assert "No model artifact found" in message
    assert "train_kaggle.py" in message


def test_development_artifact_is_flagged(tmp_path):
    # Copy the synthetic graph but mark it as a development export.
    import shutil

    source = inference.settings.onnx_path_resolved
    target = tmp_path / "cropguard_mobilenetv3.onnx"
    shutil.copyfile(source, target)
    (tmp_path / "cropguard_mobilenetv3.artifact").write_text("development_untrained\n")
    names = json.loads(inference.settings.class_names_path_resolved.read_text(encoding="utf-8"))
    (tmp_path / "class_names.json").write_text(json.dumps(names), encoding="utf-8")

    service = ModelService(
        onnx_path=target,
        torch_path=tmp_path / "none.pth",
        class_names_path=tmp_path / "class_names.json",
    )
    result = service.predict(Image.new("RGB", (224, 224), (120, 120, 120)))
    assert result.artifact_type == "development_untrained"
    assert result.is_development_artifact
    assert any("development artifact" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# Weather risk — deterministic by construction
# ---------------------------------------------------------------------------
def _snapshot(temp=28.0, humidity=60.0, rain=0.0, probability=None) -> WeatherSnapshot:
    return WeatherSnapshot(
        temperature_c=temp,
        humidity_pct=humidity,
        precipitation_mm=rain,
        precipitation_probability_pct=probability,
        wind_kph=8.0,
        observed_at="2026-10-25T12:00",
    )


def test_dry_hot_weather_is_low_risk():
    risk = compute_risk(_snapshot(temp=36.0, humidity=40.0, rain=0.0))
    assert risk.available
    assert risk.level == "Low"
    assert risk.score <= 2


def test_humid_rainy_weather_is_high_or_critical():
    risk = compute_risk(_snapshot(temp=20.0, humidity=95.0, rain=4.0, probability=85))
    assert risk.level in {"High", "Critical"}
    assert risk.score >= 5
    names = {factor.name for factor in risk.factors}
    assert {"humidity", "rainfall", "leaf_wetness"} <= names


def test_risk_is_deterministic():
    snapshot = _snapshot(temp=24.0, humidity=88.0, rain=1.2)
    first = compute_risk(snapshot).as_dict()
    second = compute_risk(snapshot).as_dict()
    assert first == second


def test_missing_weather_is_reported_not_invented():
    risk = compute_risk(None)
    assert risk.available is False
    assert risk.reason and "unavailable" in risk.reason.lower()
    assert risk.score == 0


def test_advisory_sensitivity_adjusts_the_score():
    entry = advisory.get_advisory("Potato___Late_blight")
    assert entry is not None
    snapshot = _snapshot(temp=18.0, humidity=95.0, rain=1.0)
    generic = compute_risk(snapshot)
    specific = compute_risk(snapshot, entry)
    assert specific.advisory_adjustment > 0
    assert specific.score >= generic.score
    assert specific.advisory_label == "Late blight"


def test_fetch_weather_parses_open_meteo_payload():
    payload = {
        "current": {
            "time": "2026-10-25T12:00",
            "temperature_2m": 27.4,
            "relative_humidity_2m": 88,
            "precipitation": 0.2,
            "wind_speed_10m": 11.5,
        },
        "hourly": {"precipitation_probability": [10, 20, 75, 80, 5, 0]},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert "latitude=16.5062" in str(request.url)
        return httpx.Response(200, json=payload)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        snapshot = weather_risk.fetch_weather(16.5062, 80.6480, client=http)

    assert snapshot.temperature_c == 27.4
    assert snapshot.humidity_pct == 88.0
    assert snapshot.precipitation_mm == 0.2
    assert snapshot.precipitation_probability_pct == 80.0  # max of the first 6 hours


def test_fetch_weather_raises_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(weather_risk.WeatherError):
            weather_risk.fetch_weather(16.5, 80.6, client=http)


def test_fetch_weather_raises_on_broken_payload():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"current": {}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(weather_risk.WeatherError):
            weather_risk.fetch_weather(16.5, 80.6, client=http)


def test_assess_location_degrades_when_weather_fails(monkeypatch):
    def boom(*_args, **_kwargs):
        raise weather_risk.WeatherError("network down")

    monkeypatch.setattr(weather_risk, "fetch_weather", boom)
    snapshot, risk = weather_risk.assess_location(16.5, 80.6)
    assert snapshot is None
    assert risk.available is False


# ---------------------------------------------------------------------------
# Status decision
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "confidence, predicted_crop, selected_crop, artifact, requested, expected",
    [
        (0.95, "Tomato", "Tomato", "trained", False, "AI_Verified"),
        (0.60, "Tomato", "Tomato", "trained", False, "Pending_Expert_Review"),
        (0.95, "Rice", "Tomato", "trained", False, "Pending_Expert_Review"),
        (0.95, "Tomato", "Tomato", "development_untrained", False, "Pending_Expert_Review"),
        (0.99, "Tomato", "Tomato", "trained", True, "Pending_Expert_Review"),
    ],
)
def test_status_decision_rules(
    confidence, predicted_crop, selected_crop, artifact, requested, expected
):
    status, reasons = main._decide_status(
        confidence, predicted_crop, selected_crop, artifact, requested
    )
    assert status.value == expected
    if expected == "Pending_Expert_Review":
        assert reasons, "a routed report must explain why"


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------
def test_local_storage_fallback_writes_a_file(leaf_jpeg):
    stored = storage.store_image(leaf_jpeg, "leaf.jpg", "image/jpeg")
    assert stored.backend == "local"
    assert stored.url.startswith("/uploads/")
    assert stored.warning and "Cloudinary" in stored.warning


def test_extension_resolution():
    assert storage.resolve_extension("photo.JPG", None) == ".jpg"
    assert storage.resolve_extension("photo.jpeg", None) == ".jpg"
    assert storage.resolve_extension(None, "image/png") == ".png"
    assert storage.resolve_extension(None, "image/webp") == ".webp"
    assert storage.resolve_extension("weird.gif", "image/jpeg") == ".jpg"


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
def test_root_lists_endpoints(client):
    response = client.get("/")
    assert response.status_code == 200
    payload = response.json()
    assert payload["service"] == "CropGuard API"
    assert "POST /api/analyze" in payload["endpoints"]
    assert payload["supported_crops"] == ["Tomato", "Rice", "Potato", "Wheat"]


def test_health_reports_every_component(client):
    response = client.get("/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["database"]["connected"] is True
    assert payload["model"]["available"] is True
    assert payload["model"]["backend"] == "onnx"
    assert payload["storage"]["backend"] in {"local", "cloudinary"}
    assert payload["advisory_entries"] == 21


def test_analyze_happy_path(client, leaf_jpeg):
    response = _analyze(client, leaf_jpeg)
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["report"]["predicted_disease"] == "Tomato___Early_blight"
    assert payload["report"]["status"] == "AI_Verified"
    assert payload["review_required"] is False
    assert payload["advisory"]["label"] == "Tomato___Early_blight"
    assert payload["advisory"]["cultural_control"]
    assert payload["advisory"]["chemical_management"]["recommended"] is True
    assert payload["risk"]["available"] in {True, False}
    assert "score" in payload["risk"]
    assert payload["model"]["backend"] == "onnx"
    assert payload["disclaimer"]
    # The image was stored and the URL is reachable through the static mount.
    image_url = payload["report"]["image_url"]
    assert image_url.startswith("/uploads/")
    assert client.get(image_url).status_code == 200


def test_analyze_persists_and_lists(client, leaf_jpeg):
    created = _analyze(client, leaf_jpeg).json()["report"]
    listing = client.get("/api/reports").json()
    assert listing["total"] == 1
    assert listing["items"][0]["id"] == created["id"]

    fetched = client.get(f"/api/reports/{created['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["farmer_name"] == "Ravi Kumar"
    assert fetched.json()["follow_ups"] == []


def test_analyze_rejects_unsupported_crop(client, leaf_jpeg):
    response = _analyze(client, leaf_jpeg, crop_type="Banana")
    assert response.status_code == 422
    assert "Unsupported crop" in response.json()["detail"]


def test_analyze_rejects_non_image(client):
    response = client.post(
        "/api/analyze",
        files={"image": ("notes.txt", b"hello there", "text/plain")},
        data={
            "crop_type": "Tomato",
            "farmer_name": "Ravi",
            "latitude": "16.5",
            "longitude": "80.6",
        },
    )
    assert response.status_code == 400
    assert "Unsupported image type" in response.json()["detail"]


def test_analyze_rejects_corrupt_image(client):
    response = client.post(
        "/api/analyze",
        files={"image": ("leaf.jpg", b"not really a jpeg", "image/jpeg")},
        data={
            "crop_type": "Tomato",
            "farmer_name": "Ravi",
            "latitude": "16.5",
            "longitude": "80.6",
        },
    )
    assert response.status_code == 400
    assert "not a readable image" in response.json()["detail"]


def test_analyze_rejects_missing_gps(client, leaf_jpeg):
    response = client.post(
        "/api/analyze",
        files={"image": ("leaf.jpg", leaf_jpeg, "image/jpeg")},
        data={"crop_type": "Tomato", "farmer_name": "Ravi"},
    )
    assert response.status_code == 422


def test_analyze_routes_to_expert_on_crop_mismatch(client, leaf_jpeg):
    payload = _analyze(client, leaf_jpeg, crop_type="Rice").json()
    assert payload["report"]["status"] == "Pending_Expert_Review"
    assert payload["review_required"] is True
    assert any("selected" in reason for reason in payload["review_reasons"])


def test_analyze_routes_to_expert_on_request(client, leaf_jpeg):
    payload = _analyze(client, leaf_jpeg, request_expert_review="true").json()
    assert payload["report"]["status"] == "Pending_Expert_Review"
    assert any("asked for an expert" in reason for reason in payload["review_reasons"])


def test_missing_report_returns_404(client):
    response = client.get("/api/reports/999999")
    assert response.status_code == 404
    assert "No report with id" in response.json()["detail"]


def test_expert_confirm(client, leaf_jpeg):
    report = _analyze(client, leaf_jpeg, request_expert_review="true").json()["report"]
    response = client.patch(
        f"/api/reports/{report['id']}/verify",
        json={"action": "confirm", "expert_name": "Dr Rao", "expert_notes": "Textbook early blight."},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "Expert_Confirmed"
    assert payload["final_diagnosis"] == "Tomato___Early_blight"
    assert payload["expert_name"] == "Dr Rao"
    assert payload["verified_at"] is not None
    # The AI prediction is preserved alongside the expert's decision.
    assert payload["predicted_disease"] == "Tomato___Early_blight"


def test_expert_correct(client, leaf_jpeg):
    report = _analyze(client, leaf_jpeg, request_expert_review="true").json()["report"]
    response = client.patch(
        f"/api/reports/{report['id']}/verify",
        json={
            "action": "correct",
            "corrected_disease": "Tomato___Late_blight",
            "expert_name": "Dr Rao",
            "expert_notes": "Water-soaked margins; not early blight.",
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "Expert_Corrected"
    assert payload["final_diagnosis"] == "Tomato___Late_blight"
    assert payload["predicted_disease"] == "Tomato___Early_blight"


def test_expert_correction_must_match_the_crop(client, leaf_jpeg):
    report = _analyze(client, leaf_jpeg, request_expert_review="true").json()["report"]
    response = client.patch(
        f"/api/reports/{report['id']}/verify",
        json={"action": "correct", "corrected_disease": "Rice___Brown_spot", "expert_name": "Dr Rao"},
    )
    assert response.status_code == 422
    assert "does not belong to Tomato" in response.json()["detail"]


def test_second_review_is_rejected(client, leaf_jpeg):
    report = _analyze(client, leaf_jpeg, request_expert_review="true").json()["report"]
    first = client.patch(
        f"/api/reports/{report['id']}/verify",
        json={"action": "confirm", "expert_name": "Dr Rao"},
    )
    assert first.status_code == 200

    second = client.patch(
        f"/api/reports/{report['id']}/verify",
        json={"action": "confirm", "expert_name": "Dr Rao"},
    )
    assert second.status_code == 409


def test_follow_up_creates_a_timeline_entry(client, leaf_jpeg):
    report = _analyze(client, leaf_jpeg).json()["report"]

    response = client.post(
        f"/api/reports/{report['id']}/followup",
        data={"condition_status": "Worsening", "farmer_notes": "Spots spreading to upper leaves"},
    )
    assert response.status_code == 201, response.text
    followup = response.json()
    assert followup["condition_status"] == "Worsening"
    assert followup["report_id"] == report["id"]

    detail = client.get(f"/api/reports/{report['id']}").json()
    assert len(detail["follow_ups"]) == 1
    assert detail["follow_ups"][0]["farmer_notes"] == "Spots spreading to upper leaves"


def test_follow_up_with_image(client, leaf_jpeg):
    report = _analyze(client, leaf_jpeg).json()["report"]
    response = client.post(
        f"/api/reports/{report['id']}/followup",
        data={"condition_status": "Recovering"},
        files={"image": ("followup.jpg", leaf_jpeg, "image/jpeg")},
    )
    assert response.status_code == 201
    assert response.json()["followup_image_url"].startswith("/uploads/")


def test_follow_up_rejects_invalid_condition(client, leaf_jpeg):
    report = _analyze(client, leaf_jpeg).json()["report"]
    response = client.post(
        f"/api/reports/{report['id']}/followup",
        data={"condition_status": "Improving"},
    )
    assert response.status_code == 422


def test_follow_up_on_missing_report(client):
    response = client.post(
        "/api/reports/999999/followup", data={"condition_status": "Recovering"}
    )
    assert response.status_code == 404


def test_stats_aggregate(client, leaf_jpeg):
    _analyze(client, leaf_jpeg)
    _analyze(client, leaf_jpeg, request_expert_review="true")

    response = client.get("/api/stats")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_reports"] == 2
    assert payload["pending_review"] == 1
    assert payload["ai_verified"] == 1
    assert payload["by_crop"]["Tomato"] == 2
    assert payload["reports_last_7_days"] == 2
    assert payload["top_diagnoses"][0]["label"] == "Tomato___Early_blight"


def test_report_filters(client, leaf_jpeg):
    _analyze(client, leaf_jpeg)
    _analyze(client, leaf_jpeg, request_expert_review="true")

    assert client.get("/api/reports", params={"status": "AI_Verified"}).json()["total"] == 1
    assert client.get("/api/reports", params={"status": "Pending_Expert_Review"}).json()["total"] == 1
    assert client.get("/api/reports", params={"crop_type": "Tomato"}).json()["total"] == 2
    assert client.get("/api/reports", params={"crop_type": "Rice"}).json()["total"] == 0
    assert client.get("/api/reports", params={"region_name": "Vijaya"}).json()["total"] == 2
    assert client.get("/api/reports", params={"disease": "Tomato___Late_blight"}).json()["total"] == 0


def test_reports_pagination(client, leaf_jpeg):
    for _ in range(3):
        _analyze(client, leaf_jpeg)
    page = client.get("/api/reports", params={"limit": 2, "offset": 0}).json()
    assert page["total"] == 3
    assert len(page["items"]) == 2
    page2 = client.get("/api/reports", params={"limit": 2, "offset": 2}).json()
    assert len(page2["items"]) == 1


def test_model_path_pointing_at_onnx_is_honoured(tmp_path):
    """A deployment may set MODEL_PATH to the .onnx; that must not confuse the message."""
    service = ModelService(
        torch_path=tmp_path / "cropguard_mobilenetv3.onnx",
        onnx_path=tmp_path / "cropguard_mobilenetv3.onnx",
        class_names_path=tmp_path / "class_names.json",
    )
    assert service.onnx_path.suffix == ".onnx"
    assert service.torch_path.suffix == ".pth"

    with pytest.raises(ModelUnavailable) as error:
        service.load()
    message = str(error.value)
    assert "cropguard_mobilenetv3.onnx" in message
    assert "cropguard_mobilenetv3.pth" in message
    assert message.count("(ONNX) or") == 0  # the old, confusing wording is gone


def test_head_root_answers(client):
    assert client.head("/").status_code == 200


def test_label_mismatch_is_refused(tmp_path):
    """A 4-class graph paired with a 3-label list must not load.

    Without this guard the indices would still be in range and the API would return a
    confident diagnosis with the wrong disease name.
    """
    import shutil

    source = inference.settings.onnx_path_resolved
    target = tmp_path / "cropguard_mobilenetv3.onnx"
    shutil.copyfile(source, target)
    (tmp_path / "class_names.json").write_text(
        json.dumps(["Tomato___Early_blight", "Tomato___Late_blight", "Tomato___healthy"]),
        encoding="utf-8",
    )

    service = ModelService(
        onnx_path=target,
        torch_path=tmp_path / "none.pth",
        class_names_path=tmp_path / "class_names.json",
    )
    with pytest.raises(ModelUnavailable) as error:
        service.load()
    message = str(error.value)
    assert "Label mismatch" in message
    assert "4 classes" in message and "3" in message
