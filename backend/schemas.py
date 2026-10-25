"""Pydantic v2 request/response schemas.

These are the API's contract. Anything the client sends is validated here before it
reaches a database session, and anything the API returns is shaped here so the
frontend never has to guess.

Disease labels are validated against the advisory knowledge base, which is the
single definition of what CropGuard supports. That means an unknown label is
rejected at the edge with a 422 rather than stored and silently un-adviseable.
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from advisory import supported_diseases
from models import CROP_TYPES, FollowUpCondition, ReportStatus, WeatherRiskLevel

CropType = Literal["Tomato", "Rice", "Potato", "Wheat"]


def _supported_labels() -> List[str]:
    return supported_diseases()


def diseases_for_crop(crop: str) -> List[str]:
    """Supported labels for one crop, e.g. all 'Tomato___*' entries."""
    prefix = f"{crop}___".lower()
    return [label for label in _supported_labels() if label.lower().startswith(prefix)]


def _validate_disease(value: Optional[str], crop: Optional[str] = None) -> Optional[str]:
    if value is None:
        return None
    if value not in _supported_labels():
        raise ValueError(
            f"'{value}' is not a supported class. Supported labels: "
            + ", ".join(_supported_labels())
        )
    if crop and not value.lower().startswith(f"{crop.lower()}___"):
        raise ValueError(f"'{value}' does not belong to crop '{crop}'")
    return value


# ---------------------------------------------------------------------------
# Follow-ups
# ---------------------------------------------------------------------------
class FollowUpCreate(BaseModel):
    """A farmer's later observation. The image is optional."""

    condition_status: FollowUpCondition = Field(
        ..., description="Recovering, No_Change or Worsening"
    )
    farmer_notes: Optional[str] = Field(
        default=None, max_length=2000, description="Free-text observation from the farmer"
    )
    followup_image_url: Optional[str] = Field(
        default=None, max_length=512, description="Set by the API after upload, not by the client"
    )

    @field_validator("farmer_notes")
    @classmethod
    def _strip_notes(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class FollowUpOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    report_id: int
    condition_status: FollowUpCondition
    farmer_notes: Optional[str] = None
    followup_image_url: Optional[str] = None
    created_at: datetime


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------
class ReportCreate(BaseModel):
    """Internal payload used when the API persists a freshly analysed report."""

    farmer_name: str = Field(..., min_length=2, max_length=120)
    crop_type: CropType
    image_url: str = Field(..., min_length=1, max_length=512)
    predicted_disease: str = Field(..., max_length=120)
    confidence: float = Field(..., ge=0.0, le=1.0)
    top_predictions: Optional[List[Dict[str, object]]] = None
    model_version: Optional[str] = Field(default=None, max_length=64)

    weather_temp: Optional[float] = Field(default=None, ge=-90, le=70)
    weather_humidity: Optional[float] = Field(default=None, ge=0, le=100)
    weather_rain: Optional[float] = Field(default=None, ge=0)
    weather_risk_level: Optional[WeatherRiskLevel] = None

    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    region_name: Optional[str] = Field(default=None, max_length=160)

    status: ReportStatus = ReportStatus.PENDING_EXPERT_REVIEW

    @field_validator("predicted_disease")
    @classmethod
    def _known_disease(cls, value: str) -> str:
        return _validate_disease(value)  # type: ignore[return-value]

    @field_validator("farmer_name", "region_name")
    @classmethod
    def _strip(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class ReportSummary(BaseModel):
    """Compact shape for lists and the outbreak map."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    crop_type: str
    predicted_disease: str
    final_diagnosis: Optional[str] = None
    confidence: float
    weather_risk_level: Optional[WeatherRiskLevel] = None
    latitude: float
    longitude: float
    region_name: Optional[str] = None
    status: ReportStatus
    created_at: datetime

    @property
    def effective_diagnosis(self) -> str:
        return self.final_diagnosis or self.predicted_disease


class ReportOut(ReportSummary):
    """Full report, including advisory and follow-up history."""

    farmer_name: str
    image_url: str
    top_predictions: Optional[List[Dict[str, object]]] = None
    model_version: Optional[str] = None
    weather_temp: Optional[float] = None
    weather_humidity: Optional[float] = None
    weather_rain: Optional[float] = None
    expert_notes: Optional[str] = None
    expert_name: Optional[str] = None
    verified_at: Optional[datetime] = None
    follow_ups: List[FollowUpOut] = Field(default_factory=list)


class ReportListOut(BaseModel):
    """Paginated report list."""

    total: int
    limit: int
    offset: int
    items: List[ReportSummary]


# ---------------------------------------------------------------------------
# Expert verification
# ---------------------------------------------------------------------------
class VerifyReportRequest(BaseModel):
    """An expert either confirms the AI's diagnosis or replaces it."""

    action: Literal["confirm", "correct"]
    corrected_disease: Optional[str] = Field(
        default=None, description="Required when action is 'correct'"
    )
    expert_notes: Optional[str] = Field(default=None, max_length=2000)
    expert_name: str = Field(..., min_length=2, max_length=120)

    @field_validator("expert_notes", "expert_name")
    @classmethod
    def _strip(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @model_validator(mode="after")
    def _check_action(self) -> "VerifyReportRequest":
        if self.action == "correct":
            if not self.corrected_disease:
                raise ValueError("corrected_disease is required when action is 'correct'")
            _validate_disease(self.corrected_disease)
        elif self.corrected_disease:
            raise ValueError(
                "corrected_disease must not be sent when action is 'confirm'; "
                "use action 'correct' to change the diagnosis"
            )
        return self


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------
class StatsResponse(BaseModel):
    """Aggregate counters for the dashboard strip on the map page."""

    total_reports: int
    pending_review: int
    ai_verified: int
    expert_confirmed: int
    expert_corrected: int
    follow_ups_total: int
    reports_last_7_days: int
    by_crop: Dict[str, int] = Field(default_factory=dict)
    by_risk: Dict[str, int] = Field(default_factory=dict)
    top_diagnoses: List[Dict[str, object]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Advisory (served alongside a diagnosis)
# ---------------------------------------------------------------------------
class AdvisoryOut(BaseModel):
    """The advisory block attached to a diagnosis."""

    label: str
    crop: str
    condition: str
    is_healthy: bool
    summary: str
    symptoms: List[str] = Field(default_factory=list)
    cultural_control: List[str] = Field(default_factory=list)
    organic_management: List[str] = Field(default_factory=list)
    chemical_management: Dict[str, object] = Field(default_factory=dict)
    monitoring: Optional[str] = None
    disclaimer: str = ""


# ---------------------------------------------------------------------------
# Analyze response
# ---------------------------------------------------------------------------
class AnalyzeResponse(BaseModel):
    """Everything a farmer needs after one photo, in one payload."""

    report: ReportOut
    advisory: Optional[AdvisoryOut] = None
    weather: Optional[Dict[str, object]] = None
    risk: Dict[str, object] = Field(default_factory=dict)
    review_required: bool = False
    review_reasons: List[str] = Field(default_factory=list)
    model: Dict[str, object] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)
    disclaimer: str = ""


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
class ErrorResponse(BaseModel):
    """Uniform error body so the frontend can always show something human-readable."""

    detail: str
    code: Optional[str] = None
