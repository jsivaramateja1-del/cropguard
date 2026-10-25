"""Phase 2 verification: knowledge base, database models and validation schemas.

Run from backend/:
    python -m pytest tests -v
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import select

import advisory
import database
import models
import schemas
from models import FollowUp, FollowUpCondition, Report, ReportStatus, WeatherRiskLevel

CANONICAL_PATH = Path(__file__).resolve().parents[2] / "ml_training" / "class_names_canonical.json"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session", autouse=True)
def _create_schema():
    database.init_db()
    yield


@pytest.fixture()
def session():
    db = database.SessionLocal()
    try:
        yield db
    finally:
        db.rollback()
        db.close()


@pytest.fixture()
def sample_report(session) -> Report:
    report = Report(
        farmer_name="Ravi Kumar",
        crop_type="Tomato",
        image_url="https://res.cloudinary.com/demo/leaf.jpg",
        predicted_disease="Tomato___Early_blight",
        confidence=0.62,
        top_predictions=[
            {"label": "Tomato___Early_blight", "probability": 0.62},
            {"label": "Tomato___Septoria_leaf_spot", "probability": 0.21},
        ],
        model_version="mobilenet_v3_large/trained",
        weather_temp=27.4,
        weather_humidity=88.0,
        weather_rain=0.0,
        weather_risk_level=WeatherRiskLevel.HIGH,
        latitude=16.5062,
        longitude=80.6480,
        region_name="Vijayawada, Andhra Pradesh",
        status=ReportStatus.PENDING_EXPERT_REVIEW,
    )
    session.add(report)
    session.commit()
    return report


# ---------------------------------------------------------------------------
# Advisory knowledge base
# ---------------------------------------------------------------------------
def test_advisory_covers_every_canonical_class():
    canonical = json.loads(CANONICAL_PATH.read_text(encoding="utf-8"))["classes"]
    labels = advisory.supported_diseases()

    assert len(labels) == len(canonical) == 21
    assert set(labels) == set(canonical), (
        "advisory_db.json and class_names_canonical.json disagree: "
        f"missing={set(canonical) - set(labels)} extra={set(labels) - set(canonical)}"
    )


def test_every_entry_has_the_required_sections():
    entries = advisory.load_advisory_db()["entries"]
    for label, entry in entries.items():
        for key in advisory.REQUIRED_ENTRY_KEYS:
            assert key in entry, f"{label} is missing '{key}'"
        assert isinstance(entry["cultural_control"], list)
        assert isinstance(entry["organic_management"], list)
        assert isinstance(entry["chemical_management"], dict)
        assert "recommended" in entry["chemical_management"], f"{label}: chemical_management.recommended"
        assert entry["crop"] in models.CROP_TYPES, f"{label}: crop '{entry['crop']}' not in CROP_TYPES"
        assert label.startswith(f"{entry['crop']}___"), f"{label}: label does not match its crop"


def test_diseased_entries_carry_advice_and_healthy_ones_do_not_offer_chemicals():
    for label, entry in advisory.load_advisory_db()["entries"].items():
        if entry["is_healthy"]:
            assert entry["chemical_management"]["recommended"] is False, label
        else:
            assert entry["cultural_control"], f"{label}: no cultural control steps"
            assert entry["symptoms"], f"{label}: no symptoms listed"


def test_knowledge_base_contains_no_dosage_figures():
    """The product must never invent a dose. Catching this here keeps that honest."""
    dosage_pattern = re.compile(
        r"\b\d+(\.\d+)?\s*(g|kg|ml|l|litre|liter|gm|mg)\s*(/|per)\s*(l|litre|liter|acre|ha|hectare)\b",
        re.IGNORECASE,
    )
    raw = json.dumps(advisory.load_advisory_db()["entries"])
    matches = dosage_pattern.findall(raw)
    assert not matches, f"advisory DB appears to contain dosage figures: {matches[:5]}"


def test_disclaimer_mentions_expert_and_label():
    text = advisory.disclaimer().lower()
    assert "label" in text
    assert "extension" in text or "expert" in text
    assert "not a prescription" in text


def test_unknown_label_returns_none():
    assert advisory.get_advisory("Tomato___Not_A_Real_Disease") is None


def test_advisory_for_crop_filters_correctly():
    tomato = advisory.advisory_for_crop("Tomato")
    assert len(tomato) == 10
    assert all(label.startswith("Tomato___") for label in tomato)


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
def test_schema_created_with_expected_tables():
    from sqlalchemy import inspect

    inspector = inspect(database.engine)
    tables = set(inspector.get_table_names())
    assert {"reports", "follow_ups"} <= tables

    report_columns = {column["name"] for column in inspector.get_columns("reports")}
    for required in (
        "farmer_name",
        "crop_type",
        "image_url",
        "predicted_disease",
        "confidence",
        "weather_temp",
        "weather_humidity",
        "weather_rain",
        "weather_risk_level",
        "latitude",
        "longitude",
        "region_name",
        "status",
        "final_diagnosis",
        "expert_notes",
        "created_at",
    ):
        assert required in report_columns, f"reports.{required} is missing"


def test_database_info_never_leaks_credentials():
    info = database.database_info()
    assert set(info) == {"dialect", "target"}
    assert "password" not in json.dumps(info).lower()


def test_report_round_trip_and_enum_values(session, sample_report):
    fetched = session.get(Report, sample_report.id)
    assert fetched is not None
    assert fetched.crop_type == "Tomato"
    assert fetched.status is ReportStatus.PENDING_EXPERT_REVIEW
    assert fetched.weather_risk_level is WeatherRiskLevel.HIGH
    assert fetched.created_at is not None
    assert fetched.top_predictions[0]["label"] == "Tomato___Early_blight"

    # Stored as the human-readable value, not the Python member name.
    raw = session.execute(select(Report.status).where(Report.id == fetched.id)).scalar_one()
    assert raw == "Pending_Expert_Review"


def test_effective_diagnosis_prefers_expert(session, sample_report):
    assert sample_report.effective_diagnosis == "Tomato___Early_blight"
    sample_report.final_diagnosis = "Tomato___Septoria_leaf_spot"
    session.commit()
    assert sample_report.effective_diagnosis == "Tomato___Septoria_leaf_spot"


def test_follow_ups_are_ordered_and_cascade_on_delete(session):
    report = Report(
        farmer_name="Lakshmi Devi",
        crop_type="Rice",
        image_url="https://res.cloudinary.com/demo/rice.jpg",
        predicted_disease="Rice___Brown_spot",
        confidence=0.81,
        latitude=16.9891,
        longitude=82.2475,
        region_name="Rajahmundry, Andhra Pradesh",
        status=ReportStatus.AI_VERIFIED,
    )
    session.add(report)
    session.commit()

    base = datetime.now(timezone.utc)
    session.add_all(
        [
            FollowUp(report_id=report.id, condition_status=FollowUpCondition.WORSENING,
                     farmer_notes="Spots spreading", created_at=base + timedelta(days=2)),
            FollowUp(report_id=report.id, condition_status=FollowUpCondition.RECOVERING,
                     farmer_notes="New leaves look clean", created_at=base + timedelta(days=5)),
        ]
    )
    session.commit()

    session.refresh(report)
    assert [f.condition_status for f in report.follow_ups] == [
        FollowUpCondition.WORSENING,
        FollowUpCondition.RECOVERING,
    ]

    report_id = report.id
    session.delete(report)
    session.commit()
    remaining = session.execute(select(FollowUp).where(FollowUp.report_id == report_id)).scalars().all()
    assert remaining == [], "follow-ups should be deleted with their report"


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
def test_report_create_accepts_a_valid_payload():
    payload = schemas.ReportCreate(
        farmer_name="  Suresh  ",
        crop_type="Tomato",
        image_url="/uploads/leaf.jpg",
        predicted_disease="Tomato___Late_blight",
        confidence=0.94,
        latitude=16.5,
        longitude=80.6,
        region_name="  Guntur  ",
    )
    assert payload.farmer_name == "Suresh"
    assert payload.region_name == "Guntur"
    assert payload.status is ReportStatus.PENDING_EXPERT_REVIEW


@pytest.mark.parametrize(
    "overrides",
    [
        {"latitude": 120.0},
        {"longitude": -200.0},
        {"confidence": 1.4},
        {"predicted_disease": "Tomato___Imaginary_blight"},
        {"crop_type": "Banana"},
        {"farmer_name": "A"},
    ],
)
def test_report_create_rejects_invalid_payloads(overrides):
    base = dict(
        farmer_name="Suresh",
        crop_type="Tomato",
        image_url="/uploads/leaf.jpg",
        predicted_disease="Tomato___Late_blight",
        confidence=0.9,
        latitude=16.5,
        longitude=80.6,
    )
    base.update(overrides)
    with pytest.raises(ValidationError):
        schemas.ReportCreate(**base)


def test_verify_request_confirm_path():
    request = schemas.VerifyReportRequest(action="confirm", expert_name="Dr Rao")
    assert request.corrected_disease is None

    with pytest.raises(ValidationError):
        schemas.VerifyReportRequest(
            action="confirm",
            corrected_disease="Tomato___Late_blight",
            expert_name="Dr Rao",
        )


def test_verify_request_correct_path():
    request = schemas.VerifyReportRequest(
        action="correct",
        corrected_disease="Tomato___Septoria_leaf_spot",
        expert_name="Dr Rao",
        expert_notes="Concentric rings absent; pycnidia visible under hand lens.",
    )
    assert request.corrected_disease == "Tomato___Septoria_leaf_spot"

    with pytest.raises(ValidationError):
        schemas.VerifyReportRequest(action="correct", expert_name="Dr Rao")  # no disease

    with pytest.raises(ValidationError):
        schemas.VerifyReportRequest(
            action="correct", corrected_disease="Tomato___Imaginary_blight", expert_name="Dr Rao"
        )  # unsupported label

    # A label from a different crop is structurally valid here: the request schema
    # has no knowledge of which report it targets. Cross-crop rejection happens in
    # the endpoint, which validates against the report's own crop_type.
    cross_crop = schemas.VerifyReportRequest(
        action="correct", corrected_disease="Rice___Brown_spot", expert_name="Dr Rao"
    )
    assert cross_crop.corrected_disease == "Rice___Brown_spot"


def test_diseases_for_crop_helper():
    tomato = schemas.diseases_for_crop("Tomato")
    assert len(tomato) == 10
    assert "Tomato___Late_blight" in tomato
    assert all(label.startswith("Tomato___") for label in tomato)


def test_follow_up_create_strips_and_validates():
    payload = schemas.FollowUpCreate(condition_status="Recovering", farmer_notes="   better now  ")
    assert payload.farmer_notes == "better now"

    blank = schemas.FollowUpCreate(condition_status="No_Change", farmer_notes="   ")
    assert blank.farmer_notes is None

    with pytest.raises(ValidationError):
        schemas.FollowUpCreate(condition_status="Improving")  # not a valid status


def test_report_out_serialises_from_orm(session, sample_report):
    session.refresh(sample_report)
    out = schemas.ReportOut.model_validate(sample_report)
    assert out.id == sample_report.id
    assert out.status is ReportStatus.PENDING_EXPERT_REVIEW
    assert out.effective_diagnosis == "Tomato___Early_blight"
    assert out.follow_ups == []
    assert json.loads(out.model_dump_json())["crop_type"] == "Tomato"


def test_advisory_out_schema_round_trip():
    entry = advisory.get_advisory("Potato___Late_blight")
    assert entry is not None
    out = schemas.AdvisoryOut(
        label="Potato___Late_blight",
        crop=entry["crop"],
        condition=entry["condition"],
        is_healthy=entry["is_healthy"],
        summary=entry["summary"],
        symptoms=entry["symptoms"],
        cultural_control=entry["cultural_control"],
        organic_management=entry["organic_management"],
        chemical_management=entry["chemical_management"],
        monitoring=entry.get("monitoring"),
        disclaimer=advisory.disclaimer(),
    )
    assert out.is_healthy is False
    assert out.chemical_management["recommended"] is True
