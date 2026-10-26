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
from config import settings
from inference import InvalidImage, ModelService, ModelUnavailable, open_image, preprocess_image
from weather_risk import WeatherSnapshot, compute_risk

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
EXPERT_AUTH = {"Authorization": "Bearer test-expert-token"}
ADMIN_AUTH = {"Authorization": "Bearer test-admin-token"}

# Report id -> the one-time access token returned at creation. Tests use this the way a
# farmer's browser would; the id alone deliberately grants nothing.
TOKENS: dict = {}


def _owner(report_id: int) -> dict:
    return {"X-Report-Token": TOKENS[report_id]}


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
def _clear_weather_cache():
    weather_risk.clear_weather_cache()
    yield
    weather_risk.clear_weather_cache()


@pytest.fixture(autouse=True)
def _clean_reports():
    """Each test starts from an empty reports table."""
    with database.SessionLocal() as session:
        session.query(models.ReportImage).delete()
        session.query(models.FollowUp).delete()
        session.query(models.AuditEvent).delete()
        session.query(models.Report).delete()
        session.commit()
    TOKENS.clear()
    yield


def _analyze(client: TestClient, leaf_jpeg: bytes, **overrides):
    """POST /api/analyze and remember the access token the response returns."""
    data = {
        "crop_type": "Tomato",
        "farmer_name": "Ravi Kumar",
        "latitude": "16.5062",
        "longitude": "80.6480",
        "region_name": "Vijayawada",
    }
    data.update({k: str(v) for k, v in overrides.items()})
    response = client.post(
        "/api/analyze",
        files={"image": ("leaf.jpg", leaf_jpeg, "image/jpeg")},
        data=data,
    )
    if response.status_code == 200:
        payload = response.json()
        TOKENS[payload["report"]["id"]] = payload.get("access_token")
    return response


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
            weather_risk.fetch_weather(16.5, 80.6, client=http, retries=0)


def test_fetch_weather_raises_on_broken_payload():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"current": {}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(weather_risk.WeatherError):
            weather_risk.fetch_weather(16.5, 80.6, client=http, retries=0)


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
def test_local_storage_fallback_writes_a_file(leaf_jpeg, monkeypatch, tmp_path):
    """With database storage disabled, the disk fallback still works and says so."""
    monkeypatch.setattr(settings, "force_local_storage", True, raising=False)
    stored = storage.store_image(leaf_jpeg, "leaf.jpg", "image/jpeg")
    assert stored.backend == "local"
    assert stored.url.startswith("/uploads/")
    assert stored.warning, "the fallback must warn that the file is not durable"
    written = settings.uploads_dir / stored.url.rsplit("/", 1)[-1]
    assert written.is_file() and written.stat().st_size == len(leaf_jpeg)


def test_database_storage_is_the_default(leaf_jpeg):
    """A configured database is preferred over local disk, so images survive a redeploy."""
    stored = storage.store_image(leaf_jpeg, "leaf.jpg", "image/jpeg")
    assert stored.backend == "postgres"
    assert stored.url.startswith("/api/images/")
    assert stored.warning is None


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
    assert payload["checks"]["database"]["ok"] is True
    assert payload["checks"]["model"]["ok"] is True
    assert payload["checks"]["model"]["backend"] == "onnx"
    assert payload["checks"]["storage"]["backend"] in {"local", "cloudinary", "postgres"}
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
    assert image_url.startswith("/api/images/")
    assert client.get(image_url).status_code == 200


def test_analyze_persists_and_lists(client, leaf_jpeg):
    created = _analyze(client, leaf_jpeg).json()["report"]
    listing = client.get("/api/reports", headers=EXPERT_AUTH).json()
    assert listing["total"] == 1
    assert listing["items"][0]["id"] == created["id"]

    fetched = client.get(f"/api/reports/{created['id']}", headers=_owner(created['id']))
    assert fetched.status_code == 200
    assert fetched.json()["farmer_name"] == "Ravi Kumar"
    assert fetched.json()["follow_ups"] == []


def test_analyze_rejects_unsupported_crop(client, leaf_jpeg):
    response = _analyze(client, leaf_jpeg, crop_type="Banana")
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "Unknown crop" in detail and "Banana" in detail


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
    # 415 is the correct status for a wrong media type, and the declared type is checked
    # before the bytes are decoded.
    assert response.status_code == 415
    assert "not an accepted image type" in response.json()["detail"]


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
        headers=EXPERT_AUTH,
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
        headers=EXPERT_AUTH,
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
        headers=EXPERT_AUTH,
    )
    assert response.status_code == 422
    assert "does not belong to Tomato" in response.json()["detail"]


def test_second_review_is_rejected(client, leaf_jpeg):
    report = _analyze(client, leaf_jpeg, request_expert_review="true").json()["report"]
    first = client.patch(
        f"/api/reports/{report['id']}/verify",
        json={"action": "confirm", "expert_name": "Dr Rao"},
        headers=EXPERT_AUTH,
    )
    assert first.status_code == 200

    second = client.patch(
        f"/api/reports/{report['id']}/verify",
        json={"action": "confirm", "expert_name": "Dr Rao"},
        headers=EXPERT_AUTH,
    )
    assert second.status_code == 409


def test_follow_up_creates_a_timeline_entry(client, leaf_jpeg):
    report = _analyze(client, leaf_jpeg).json()["report"]

    response = client.post(
        f"/api/reports/{report['id']}/followup",
        headers=_owner(report['id']),
        data={"condition_status": "Worsening", "farmer_notes": "Spots spreading to upper leaves"},
    )
    assert response.status_code == 201, response.text
    followup = response.json()
    assert followup["condition_status"] == "Worsening"
    assert followup["report_id"] == report["id"]

    detail = client.get(f"/api/reports/{report['id']}", headers=_owner(report['id'])).json()
    assert len(detail["follow_ups"]) == 1
    assert detail["follow_ups"][0]["farmer_notes"] == "Spots spreading to upper leaves"


def test_follow_up_with_image(client, leaf_jpeg):
    report = _analyze(client, leaf_jpeg).json()["report"]
    response = client.post(
        f"/api/reports/{report['id']}/followup",
        headers=_owner(report['id']),
        data={"condition_status": "Recovering"},
        files={"image": ("followup.jpg", leaf_jpeg, "image/jpeg")},
    )
    assert response.status_code == 201
    assert response.json()["followup_image_url"].startswith("/api/images/")


def test_follow_up_rejects_invalid_condition(client, leaf_jpeg):
    report = _analyze(client, leaf_jpeg).json()["report"]
    response = client.post(
        f"/api/reports/{report['id']}/followup",
        headers=_owner(report['id']),
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

    response = client.get("/api/stats", headers=EXPERT_AUTH)
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

    verified = client.get(
        "/api/reports", headers=EXPERT_AUTH, params={"status": "AI_Verified"}
    ).json()
    assert verified["total"] == 1
    pending = client.get(
        "/api/reports", headers=EXPERT_AUTH, params={"status": "Pending_Expert_Review"}
    ).json()
    assert pending["total"] == 1
    assert client.get("/api/reports", headers=EXPERT_AUTH, params={"crop_type": "Tomato"}).json()["total"] == 2
    assert client.get("/api/reports", headers=EXPERT_AUTH, params={"crop_type": "Rice"}).json()["total"] == 0
    assert client.get("/api/reports", headers=EXPERT_AUTH, params={"region_name": "Vijaya"}).json()["total"] == 2
    assert client.get("/api/reports", headers=EXPERT_AUTH, params={"disease": "Tomato___Late_blight"}).json()["total"] == 0


def test_reports_pagination(client, leaf_jpeg):
    for _ in range(3):
        _analyze(client, leaf_jpeg)
    page = client.get("/api/reports", headers=EXPERT_AUTH, params={"limit": 2, "offset": 0}).json()
    assert page["total"] == 3
    assert len(page["items"]) == 2
    page2 = client.get("/api/reports", headers=EXPERT_AUTH, params={"limit": 2, "offset": 2}).json()
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


def test_timestamps_carry_utc_offset(client, leaf_jpeg):
    """A naive SQLite datetime must be serialised as UTC, not as floating local time.

    Without this the browser reads '2026-10-25T18:56:19' as local and shows a report
    submitted at 18:56 UTC as 18:56 IST — 5.5 hours wrong.
    """
    created = _analyze(client, leaf_jpeg).json()["report"]
    assert created["created_at"].endswith("+00:00"), created["created_at"]

    report = _analyze(client, leaf_jpeg, request_expert_review="true").json()["report"]
    verified = client.patch(
        f"/api/reports/{report['id']}/verify",
        json={"action": "confirm", "expert_name": "Dr Rao"},
        headers=EXPERT_AUTH,
    ).json()
    assert verified["verified_at"].endswith("+00:00"), verified["verified_at"]

    followup = client.post(
        f"/api/reports/{report['id']}/followup",
        headers=_owner(report['id']),
        data={"condition_status": "Recovering"},
    ).json()
    assert followup["created_at"].endswith("+00:00"), followup["created_at"]


def test_weather_endpoint_reports_conditions(client, monkeypatch):
    from weather_risk import WeatherSnapshot

    def fake_fetch(latitude, longitude, timeout=8.0, client=None):
        return WeatherSnapshot(
            temperature_c=29.0, humidity_pct=91.0, precipitation_mm=3.0,
            precipitation_probability_pct=80.0, wind_kph=9.0, observed_at="2026-10-25T12:00",
        )

    monkeypatch.setattr(weather_risk, "fetch_weather", fake_fetch)
    response = client.get("/api/weather", params={"latitude": 16.5062, "longitude": 80.648})
    assert response.status_code == 200
    payload = response.json()
    assert payload["weather"]["temperature_c"] == 29.0
    assert payload["risk"]["available"] is True
    assert payload["risk"]["level"] in {"High", "Critical"}


def test_weather_endpoint_carries_the_failure_reason(client, monkeypatch):
    """The reason must name the cause, not just say 'unavailable'."""
    def failing_fetch(*_args, **_kwargs):
        raise weather_risk.WeatherError("The weather service returned HTTP 429.")

    monkeypatch.setattr(weather_risk, "fetch_weather", failing_fetch)
    payload = client.get(
        "/api/weather", params={"latitude": 16.5062, "longitude": 80.648}
    ).json()
    assert payload["weather"] is None
    assert payload["risk"]["available"] is False
    assert "HTTP 429" in payload["risk"]["reason"]


def test_weather_endpoint_rejects_unknown_disease(client):
    response = client.get(
        "/api/weather",
        params={"latitude": 16.5, "longitude": 80.6, "disease": "Tomato___Nope"},
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Weather resilience (HTTP 429 on shared cloud IPs)
# ---------------------------------------------------------------------------
def _payload():
    return {
        "current": {
            "time": "2026-10-25T12:00",
            "temperature_2m": 28.0,
            "relative_humidity_2m": 88,
            "precipitation": 0.0,
            "wind_speed_10m": 7.0,
        },
        "hourly": {"precipitation_probability": [10, 20, 30, 40, 5, 0]},
    }


def test_repeated_location_is_served_from_cache():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json=_payload())

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        first = weather_risk.fetch_weather(16.5062, 80.6480, client=http)
        second = weather_risk.fetch_weather(16.5062, 80.6480, client=http)

    assert first.cached is False
    assert second.cached is True
    assert len(calls) == 1, "the second call should not touch the network"


def test_cache_is_keyed_by_location():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json=_payload())

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        weather_risk.fetch_weather(16.5062, 80.6480, client=http)
        weather_risk.fetch_weather(17.0000, 81.0000, client=http)

    assert len(calls) == 2


def test_rate_limit_is_retried_then_succeeds():
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.Response(429, json={"error": "rate limited"})
        return httpx.Response(200, json=_payload())

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        snapshot = weather_risk.fetch_weather(16.5, 80.6, client=http, retries=2)

    assert snapshot.temperature_c == 28.0
    assert len(attempts) == 2


def test_persistent_rate_limit_gives_actionable_advice():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(weather_risk.WeatherError) as error:
            weather_risk.fetch_weather(16.5, 80.6, client=http, retries=1)

    message = str(error.value)
    assert "429" in message
    assert "OPEN_METEO_API_KEY" in message


def test_client_error_is_not_retried():
    """A 400 will not improve by asking again — but the other provider is still tried."""
    hosts = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        return httpx.Response(400)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(weather_risk.WeatherError):
            weather_risk.fetch_weather(16.5, 80.6, client=http, retries=3)

    assert len(hosts) == 2, f"expected one attempt per provider, got {hosts}"
    for host in set(hosts):
        assert hosts.count(host) == 1, f"{host} was retried on a 4xx"


def test_api_key_switches_to_the_customer_endpoint(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json=_payload())

    monkeypatch.setattr(weather_risk.settings, "open_meteo_api_key", "test-key-123", raising=False)
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        weather_risk.fetch_weather(16.5, 80.6, client=http)

    assert "customer-api.open-meteo.com" in seen["url"]
    assert "apikey=test-key-123" in seen["url"]


def test_health_reports_weather_configuration(client):
    payload = client.get("/health").json()
    weather = payload["checks"]["weather"]
    assert "open-meteo" in weather["providers"]
    assert isinstance(weather["cached_locations"], int)
    assert weather["api_key_configured"] is False


# ---------------------------------------------------------------------------
# MET Norway fallback (free, keyless)
# ---------------------------------------------------------------------------
MET_NO_PAYLOAD = {
    "type": "Feature",
    "properties": {
        "timeseries": [
            {
                "time": "2026-10-25T12:00:00Z",
                "data": {
                    "instant": {
                        "details": {
                            "air_temperature": 28.4,
                            "relative_humidity": 91.0,
                            "wind_speed": 3.2,
                        }
                    },
                    "next_1_hours": {
                        "summary": {"symbol_code": "rain"},
                        "details": {
                            "precipitation_amount": 1.4,
                            "probability_of_precipitation": 76.0,
                        },
                    },
                },
            },
            {
                "time": "2026-10-25T13:00:00Z",
                "data": {
                    "instant": {"details": {"air_temperature": 28.0, "relative_humidity": 90.0}},
                    "next_1_hours": {"details": {"precipitation_amount": 0.6}},
                },
            },
        ]
    },
}


def test_met_no_payload_parses():
    snapshot = weather_risk._parse_met_no(MET_NO_PAYLOAD)
    assert snapshot.temperature_c == 28.4
    assert snapshot.humidity_pct == 91.0
    assert snapshot.precipitation_mm == 2.0        # summed over the next hours
    assert snapshot.precipitation_probability_pct == 76.0
    assert snapshot.wind_kph == 11.5                # m/s converted to km/h
    assert snapshot.source == "met.no"


def test_met_no_payload_without_optional_blocks_still_parses():
    payload = {
        "properties": {
            "timeseries": [
                {"time": "2026-10-25T12:00:00Z",
                 "data": {"instant": {"details": {"air_temperature": 25.0, "relative_humidity": 70.0}}}}
            ]
        }
    }
    snapshot = weather_risk._parse_met_no(payload)
    assert snapshot.temperature_c == 25.0
    assert snapshot.precipitation_mm == 0.0
    assert snapshot.precipitation_probability_pct is None


def test_met_no_malformed_payload_raises():
    with pytest.raises(weather_risk.WeatherError):
        weather_risk._parse_met_no({"properties": {"timeseries": []}})
    with pytest.raises(weather_risk.WeatherError):
        weather_risk._parse_met_no({"properties": {"timeseries": [{"data": {}}]}})


def test_falls_back_to_met_no_when_open_meteo_is_rate_limited():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.host)
        if "open-meteo" in request.url.host:
            return httpx.Response(429)
        return httpx.Response(200, json=MET_NO_PAYLOAD)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        snapshot = weather_risk.fetch_weather(16.5062, 80.6480, client=http, retries=0)

    assert snapshot.source == "met.no"
    assert "api.met.no" in seen


def test_met_no_request_sends_an_identifying_user_agent():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["ua"] = request.headers.get("user-agent")
        return httpx.Response(200, json=MET_NO_PAYLOAD)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        weather_risk.fetch_weather(16.5062, 80.6480, client=http)

    assert "CropGuard" in captured["ua"]


def test_all_providers_failing_names_each_one():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(weather_risk.WeatherError) as error:
            weather_risk.fetch_weather(16.5, 80.6, client=http, retries=0)

    message = str(error.value)
    assert "open-meteo" in message and "met-no" in message
    assert "OPEN_METEO_API_KEY" in message


def test_provider_can_be_pinned(monkeypatch):
    monkeypatch.setattr(weather_risk.settings, "weather_provider", "met-no", raising=False)
    assert weather_risk._provider_order() == ["met-no"]
    monkeypatch.setattr(weather_risk.settings, "weather_provider", "auto", raising=False)
    assert weather_risk._provider_order() == ["open-meteo", "met-no"]


# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------
def test_glob_to_regex_matches_one_label_only():
    from config import glob_to_regex

    pattern = glob_to_regex("https://*.vercel.app")
    import re

    assert re.fullmatch(pattern, "https://cropguard-l7a2ktqmx-jsivaramateja1-8889.vercel.app")
    assert re.fullmatch(pattern, "https://cropguard-blush.vercel.app")
    # One label only: these must not match.
    assert not re.fullmatch(pattern, "https://a.b.vercel.app")
    assert not re.fullmatch(pattern, "https://vercel.app.evil.com")
    assert not re.fullmatch(pattern, "http://cropguard.vercel.app")


def test_configured_origin_is_allowed(client):
    response = client.get("/health", headers={"Origin": "http://localhost:5173"})
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_unknown_origin_gets_no_cors_header(client):
    response = client.get("/health", headers={"Origin": "https://not-allowed.example.com"})
    assert response.headers.get("access-control-allow-origin") is None


def test_preflight_for_analyze_is_answered(client):
    response = client.options(
        "/api/analyze",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "http://localhost:5173"
    assert "POST" in response.headers.get("access-control-allow-methods", "")


def test_wildcard_origin_regex_is_used_when_configured(monkeypatch):
    """A Vercel deployment hostname must pass when the pattern is configured."""
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.testclient import TestClient

    monkeypatch.setattr(
        main.settings, "frontend_origin", "http://localhost:5173,https://*.vercel.app",
        raising=False,
    )
    probe = FastAPI()
    probe.add_middleware(
        CORSMiddleware,
        allow_origins=main.settings.cors_origins,
        allow_origin_regex=main.settings.cors_origin_regex,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @probe.get("/ping")
    def ping():
        return {"ok": True}

    with TestClient(probe) as client:
        allowed = client.get(
            "/ping",
            headers={"Origin": "https://cropguard-l7a2ktqmx-jsivaramateja1-8889.vercel.app"},
        )
        assert allowed.headers.get("access-control-allow-origin") == (
            "https://cropguard-l7a2ktqmx-jsivaramateja1-8889.vercel.app"
        )
        blocked = client.get("/ping", headers={"Origin": "https://evil.example.com"})
        assert blocked.headers.get("access-control-allow-origin") is None


# ---------------------------------------------------------------------------
# Out-of-distribution guard
# ---------------------------------------------------------------------------
def _solid(colours, size=(240, 240)):
    import numpy as np
    from PIL import Image

    array = np.zeros((size[1], size[0], 3), dtype=np.uint8)
    band = size[0] // len(colours)
    for index, colour in enumerate(colours):
        start = index * band
        end = size[0] if index == len(colours) - 1 else (index + 1) * band
        array[:, start:end] = colour
    import io

    buffer = io.BytesIO()
    Image.fromarray(array).save(buffer, format="JPEG", quality=95)
    return buffer.getvalue()


def test_plausibility_accepts_foliage():
    from PIL import Image

    green = Image.new("RGB", (240, 240), (60, 120, 50))
    brown = Image.new("RGB", (240, 240), (140, 110, 60))
    assert inference.leaf_plausibility(green)["is_plausible_leaf"] is True
    assert inference.leaf_plausibility(brown)["is_plausible_leaf"] is True


def test_plausibility_rejects_non_plant_images():
    from PIL import Image

    for name, colour in [
        ("costume red", (200, 30, 40)),
        ("costume blue", (30, 60, 180)),
        ("sky", (120, 170, 235)),
        ("skin", (220, 170, 140)),
        ("wall", (245, 245, 240)),
    ]:
        result = inference.leaf_plausibility(Image.new("RGB", (240, 240), colour))
        assert result["is_plausible_leaf"] is False, name
        assert result["vegetation_fraction"] < result["threshold"], name


def test_implausible_photo_is_routed_and_warned(client):
    """A non-leaf photo must be flagged, routed to a human, and warned about."""
    response = client.post(
        "/api/analyze",
        files={"image": ("spiderman.jpg", _solid([(200, 30, 40), (30, 60, 180)]), "image/jpeg")},
        data={
            "crop_type": "Tomato",
            "farmer_name": "Test Farmer",
            "latitude": "16.5",
            "longitude": "80.6",
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["plausibility"]["is_plausible_leaf"] is False
    assert payload["report"]["status"] == "Pending_Expert_Review"
    assert payload["review_required"] is True
    assert any("does not look like crop foliage" in reason for reason in payload["review_reasons"])
    assert any("may not be a crop leaf" in warning for warning in payload["warnings"])


def test_plausible_leaf_is_not_flagged(client):
    response = client.post(
        "/api/analyze",
        files={"image": ("leaf.jpg", _solid([(60, 120, 50)]), "image/jpeg")},
        data={
            "crop_type": "Tomato",
            "farmer_name": "Test Farmer",
            "latitude": "16.5",
            "longitude": "80.6",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["plausibility"]["is_plausible_leaf"] is True
    assert not any("may not be a crop leaf" in warning for warning in payload["warnings"])


def test_low_confidence_adds_an_out_of_scope_reason():
    status, reasons = main._decide_status(
        confidence=0.12,
        predicted_crop="Tomato",
        selected_crop="Tomato",
        artifact_type="trained",
        farmer_requested_review=False,
        plausible_leaf=True,
    )
    assert status.value == "Pending_Expert_Review"
    assert any("outside the 16 diseases" in reason for reason in reasons)


def test_confidence_below_the_leaf_floor_is_flagged():
    """A bird photo scored 0.81 while genuine leaves score a median of 0.907."""
    status, reasons = main._decide_status(
        confidence=0.81,
        predicted_crop="Tomato",
        selected_crop="Tomato",
        artifact_type="trained",
        farmer_requested_review=False,
        plausible_leaf=True,          # the colour check passes: green foliage in frame
        vegetation_fraction=0.4,
        confidence_floor=0.88,
    )
    assert status.value == "Pending_Expert_Review"
    assert any("may not be a crop leaf" in reason for reason in reasons)


def test_confident_in_distribution_leaf_is_still_auto_verified():
    status, reasons = main._decide_status(
        confidence=0.93,
        predicted_crop="Tomato",
        selected_crop="Tomato",
        artifact_type="trained",
        farmer_requested_review=False,
        plausible_leaf=True,
        vegetation_fraction=0.7,
        confidence_floor=0.88,
    )
    assert status.value == "AI_Verified"
    assert reasons == []


def test_floor_is_configurable(monkeypatch):
    monkeypatch.setattr(main.settings, "ood_confidence_floor", 0.5, raising=False)
    status, reasons = main._decide_status(
        confidence=0.81,
        predicted_crop="Tomato",
        selected_crop="Tomato",
        artifact_type="trained",
        farmer_requested_review=False,
        plausible_leaf=True,
        vegetation_fraction=0.4,
        confidence_floor=main.settings.ood_confidence_floor,
    )
    assert status.value == "AI_Verified"
