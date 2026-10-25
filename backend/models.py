"""SQLAlchemy models: a farmer's report and its follow-up history.

Design notes
------------
* ``predicted_disease`` and ``confidence`` are written once, by the AI, and never
  mutated. An expert's decision lands in ``final_diagnosis`` and ``status``. That
  separation is what lets the product keep both the machine's opinion and the
  human's correction for later analysis.
* Enums store their human-readable values ("Pending_Expert_Review") rather than
  Python member names, so the database, the API and the UI all speak the same
  language. ``native_enum=False`` keeps the same DDL on SQLite and PostgreSQL.
* Weather columns are nullable on purpose: a weather outage must not block a
  diagnosis. ``weather_risk_level`` is NULL when the weather engine could not run.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import List, Optional

from sqlalchemy import (
    JSON,
    DateTime,
    Enum as SAEnum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base

CROP_TYPES = ("Tomato", "Rice", "Potato", "Wheat")


class ReportStatus(str, enum.Enum):
    """Lifecycle of a report."""

    AI_VERIFIED = "AI_Verified"
    PENDING_EXPERT_REVIEW = "Pending_Expert_Review"
    EXPERT_CONFIRMED = "Expert_Confirmed"
    EXPERT_CORRECTED = "Expert_Corrected"


class WeatherRiskLevel(str, enum.Enum):
    """Deterministic weather-driven disease risk (see weather_risk.py)."""

    LOW = "Low"
    MODERATE = "Moderate"
    HIGH = "High"
    CRITICAL = "Critical"


class FollowUpCondition(str, enum.Enum):
    """What the farmer reports when they check back."""

    RECOVERING = "Recovering"
    NO_CHANGE = "No_Change"
    WORSENING = "Worsening"


def _enum(enum_class, name: str) -> SAEnum:
    """Store enum *values* (not member names) in a portable VARCHAR + CHECK column.

    ``create_constraint=True`` matters: since SQLAlchemy 1.4 non-native enums do not
    emit a CHECK constraint by default, which would let a direct SQL insert write
    'Pending' or 'expert_confirmed' and poison the column. The constraint keeps the
    database honest independently of the application.
    """
    return SAEnum(
        enum_class,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=32,
        values_callable=lambda members: [member.value for member in members],
        validate_strings=True,
    )


class Report(Base):
    """One analysed leaf image."""

    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # --- who and what -------------------------------------------------------
    farmer_name: Mapped[str] = mapped_column(String(120), nullable=False)
    crop_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    image_url: Mapped[str] = mapped_column(String(512), nullable=False)

    # --- AI prediction (immutable) -----------------------------------------
    predicted_disease: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    top_predictions: Mapped[Optional[list]] = mapped_column(
        JSON,
        nullable=True,
        doc="Top-k {class, probability} pairs, kept so a corrected report still shows the AI's reasoning.",
    )
    model_version: Mapped[Optional[str]] = mapped_column(
        String(64),
        nullable=True,
        doc="Checkpoint identity, e.g. 'mobilenet_v3_large/trained'. Flags development artifacts.",
    )

    # --- weather at the moment of the report -------------------------------
    weather_temp: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    weather_humidity: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    weather_rain: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    weather_risk_level: Mapped[Optional[WeatherRiskLevel]] = mapped_column(
        _enum(WeatherRiskLevel, "weather_risk_level"), nullable=True, index=True
    )

    # --- where --------------------------------------------------------------
    latitude: Mapped[float] = mapped_column(Float, nullable=False, index=True)
    longitude: Mapped[float] = mapped_column(Float, nullable=False, index=True)
    region_name: Mapped[Optional[str]] = mapped_column(String(160), nullable=True, index=True)

    # --- expert workflow ----------------------------------------------------
    status: Mapped[ReportStatus] = mapped_column(
        _enum(ReportStatus, "report_status"),
        nullable=False,
        default=ReportStatus.PENDING_EXPERT_REVIEW,
        server_default=ReportStatus.PENDING_EXPERT_REVIEW.value,
        index=True,
    )
    final_diagnosis: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    expert_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    expert_name: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )

    follow_ups: Mapped[List["FollowUp"]] = relationship(
        back_populates="report",
        cascade="all, delete-orphan",
        order_by="FollowUp.created_at",
        passive_deletes=True,
    )

    __table_args__ = (
        Index("ix_reports_crop_status", "crop_type", "status"),
        Index("ix_reports_geo", "latitude", "longitude"),
    )

    # ------------------------------------------------------------------ helpers
    @property
    def effective_diagnosis(self) -> str:
        """The diagnosis a farmer should act on: the expert's if there is one."""
        return self.final_diagnosis or self.predicted_disease

    @property
    def is_development_artifact(self) -> bool:
        return bool(self.model_version and "development" in self.model_version)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<Report id={self.id} crop={self.crop_type!r} "
            f"predicted={self.predicted_disease!r} status={self.status.value}>"
        )


class FollowUp(Base):
    """A farmer's later observation on an existing report."""

    __tablename__ = "follow_ups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_id: Mapped[int] = mapped_column(
        ForeignKey("reports.id", ondelete="CASCADE"), nullable=False, index=True
    )

    condition_status: Mapped[FollowUpCondition] = mapped_column(
        _enum(FollowUpCondition, "followup_condition"), nullable=False, index=True
    )
    farmer_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    followup_image_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )

    report: Mapped[Report] = relationship(back_populates="follow_ups")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<FollowUp id={self.id} report_id={self.report_id} condition={self.condition_status.value}>"
