"""CropGuard API.

Flow for a single report:

    photo -> validate -> infer -> advisory -> weather risk -> store image
          -> decide status -> persist -> respond

Design notes
------------
* Every endpoint is a synchronous ``def``. Inference and database calls block, so
  FastAPI runs them in a worker thread and the event loop stays free. An ``async def``
  here would block the loop on every request.
* Failures are specific. A missing model returns 503 with an instruction, a bad image
  returns 400 with the reason, an unknown report returns 404. Nothing is swallowed.
* Weather is best-effort: if Open-Meteo is down the diagnosis still returns and the
  risk is reported as unavailable with a reason.
* The AI's prediction is stored once and never overwritten. An expert's decision lands
  in ``final_diagnosis`` and ``status``.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

import advisory as advisory_module
import schemas
import storage
from config import settings
from database import database_info, get_db, init_db
from inference import (
    InvalidImage,
    ModelUnavailable,
    get_model_service,
    open_image,
)
from models import CROP_TYPES, FollowUp, FollowUpCondition, Report, ReportStatus
from storage import ALLOWED_CONTENT_TYPES, StorageError
import weather_risk
from weather_risk import assess_location

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger("cropguard.api")

API_VERSION = "0.3.0"


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    init_db()
    logger.info("database ready: %s", database_info())

    # Warm the model so the first farmer does not pay the load cost. A missing or
    # broken artifact must not stop the API from starting — /health reports it and
    # /api/analyze returns a clear 503.
    service = get_model_service()
    try:
        service.load()
    except ModelUnavailable as error:
        logger.warning("starting without a model: %s", error)
    except Exception:
        logger.exception("unexpected error while warming the model")

    logger.info(
        "storage: %s | confidence threshold: %.2f",
        storage.storage_status()["backend"],
        settings.confidence_threshold,
    )
    yield


app = FastAPI(
    title="CropGuard API",
    version=API_VERSION,
    description=(
        "Early detection and management of crop diseases and pest infestations. "
        "AI results are a potential diagnosis and an early-screening aid, not a "
        "replacement for an agricultural expert."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    # Wildcard entries (e.g. https://*.vercel.app) become a regex, because Vercel
    # issues a fresh hostname per deployment and an exact list would break each time.
    allow_origin_regex=settings.cors_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

settings.uploads_dir.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(settings.uploads_dir)), name="uploads")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _read_upload(upload: UploadFile) -> bytes:
    """Read an upload with a hard size cap, without buffering past the limit."""
    limit = settings.max_upload_bytes
    chunks: List[bytes] = []
    total = 0
    while True:
        chunk = upload.file.read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=(
                    f"The image is larger than {settings.max_upload_mb} MB. "
                    "Take the photo at a lower resolution or compress it first."
                ),
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _validate_crop(crop: str) -> str:
    for known in CROP_TYPES:
        if crop.strip().lower() == known.lower():
            return known
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail=f"Unsupported crop '{crop}'. Supported crops: {', '.join(CROP_TYPES)}.",
    )


def _validate_content_type(upload: UploadFile) -> None:
    declared = (upload.content_type or "").split(";")[0].strip().lower()
    if declared and declared not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Unsupported image type '{declared}'. "
                "Send a JPEG, PNG or WebP photo."
            ),
        )


def _advisory_out(label: str) -> Optional[schemas.AdvisoryOut]:
    entry = advisory_module.get_advisory(label)
    if entry is None:
        return None
    return schemas.AdvisoryOut(
        label=label,
        crop=entry["crop"],
        condition=entry["condition"],
        is_healthy=entry["is_healthy"],
        summary=entry["summary"],
        symptoms=entry.get("symptoms", []),
        cultural_control=entry.get("cultural_control", []),
        organic_management=entry.get("organic_management", []),
        chemical_management=entry.get("chemical_management", {}),
        monitoring=entry.get("monitoring"),
        disclaimer=advisory_module.disclaimer(),
    )


def _load_report(db: Session, report_id: int) -> Report:
    report = db.execute(
        select(Report).options(selectinload(Report.follow_ups)).where(Report.id == report_id)
    ).scalar_one_or_none()
    if report is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No report with id {report_id}.",
        )
    return report


def _decide_status(
    confidence: float,
    predicted_crop: str,
    selected_crop: str,
    artifact_type: str,
    farmer_requested_review: bool,
) -> tuple[ReportStatus, List[str]]:
    """Which reports need a human, and why.

    Confidence alone is not enough: docs/calibration.md shows the trained model sits
    near its label-smoothing target and would route only 0.7% of reports. These
    independent triggers keep the expert queue honest and populated.
    """
    reasons: List[str] = []

    if farmer_requested_review:
        reasons.append("The farmer asked for an expert opinion.")
    if confidence < settings.confidence_threshold:
        reasons.append(
            f"Confidence {confidence:.2f} is below the {settings.confidence_threshold:.2f} threshold."
        )
    if predicted_crop.lower() != selected_crop.lower():
        reasons.append(
            f"The photo looks like {predicted_crop} but the farmer selected {selected_crop}."
        )
    if artifact_type == "development_untrained":
        reasons.append("The running model is a development artifact, not a trained one.")

    if reasons:
        return ReportStatus.PENDING_EXPERT_REVIEW, reasons
    return ReportStatus.AI_VERIFIED, reasons


# ---------------------------------------------------------------------------
# Root and health
# ---------------------------------------------------------------------------
@app.get("/", summary="Service information")
def root() -> dict:
    return {
        "service": "CropGuard API",
        "version": API_VERSION,
        "description": "Early detection and management of crop diseases and pest infestations.",
        "docs": "/docs",
        "endpoints": [
            "POST /api/analyze",
            "GET /api/reports",
            "GET /api/reports/{id}",
            "PATCH /api/reports/{id}/verify",
            "POST /api/reports/{id}/followup",
            "GET /api/weather",
            "GET /api/stats",
            "GET /health",
        ],
        "supported_crops": list(CROP_TYPES),
        "notice": (
            "AI results are an early-screening aid. Confirm with an agricultural expert "
            "when confidence is low or the situation is uncertain."
        ),
    }


@app.head("/", include_in_schema=False)
def root_head() -> None:
    """Platform health probes sometimes send HEAD /; answer instead of 405."""
    return None


@app.get("/health", summary="Health and readiness")
def health() -> dict:
    service = get_model_service()
    model = service.describe()

    db_ok = True
    db_error = None
    try:
        from database import SessionLocal

        with SessionLocal() as session:
            session.execute(select(func.count()).select_from(Report))
    except Exception as error:  # noqa: BLE001 - health must never raise
        db_ok = False
        db_error = f"{type(error).__name__}: {error}"

    overall = "ok"
    if not db_ok:
        overall = "degraded"
    elif not model["available"]:
        overall = "degraded"

    return {
        "status": overall,
        "version": API_VERSION,
        "database": {**database_info(), "connected": db_ok, "error": db_error},
        "model": model,
        "storage": storage.storage_status(),
        "weather": weather_risk.weather_status(),
        "confidence_threshold": settings.confidence_threshold,
        "advisory_entries": len(advisory_module.supported_diseases()),
    }


# ---------------------------------------------------------------------------
# Analyze
# ---------------------------------------------------------------------------
@app.post("/api/analyze", response_model=schemas.AnalyzeResponse, summary="Analyse a leaf photo")
def analyze(
    image: UploadFile = File(..., description="Leaf photo (JPEG, PNG or WebP)"),
    crop_type: str = Form(..., description="Tomato, Rice, Potato or Wheat"),
    farmer_name: str = Form(..., description="Who submitted this report"),
    latitude: float = Form(..., ge=-90, le=90),
    longitude: float = Form(..., ge=-180, le=180),
    region_name: Optional[str] = Form(default=None),
    request_expert_review: bool = Form(default=False),
    db: Session = Depends(get_db),
) -> schemas.AnalyzeResponse:
    crop = _validate_crop(crop_type)
    if not farmer_name or len(farmer_name.strip()) < 2:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="farmer_name is required and must be at least 2 characters.",
        )
    _validate_content_type(image)

    raw = _read_upload(image)
    if not raw:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="The uploaded file is empty.")

    try:
        pil_image = open_image(raw)
    except InvalidImage as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error

    service = get_model_service()
    try:
        service.load()
        prediction = service.predict(pil_image, top_k=3)
    except ModelUnavailable as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"The diagnosis model is unavailable: {error}",
        ) from error
    except InvalidImage as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error

    advisory_entry = advisory_module.get_advisory(prediction.top.label)

    weather, risk = assess_location(latitude, longitude, advisory_entry)

    try:
        stored = storage.store_image(raw, image.filename, image.content_type)
    except StorageError as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"The leaf image could not be stored: {error}",
        ) from error

    report_status, review_reasons = _decide_status(
        confidence=prediction.confidence,
        predicted_crop=prediction.top.crop,
        selected_crop=crop,
        artifact_type=prediction.artifact_type,
        farmer_requested_review=request_expert_review,
    )

    report = Report(
        farmer_name=farmer_name.strip(),
        crop_type=crop,
        image_url=stored.url,
        predicted_disease=prediction.top.label,
        confidence=round(prediction.confidence, 4),
        top_predictions=[
            {"label": p.label, "probability": round(p.probability, 4)}
            for p in prediction.predictions
        ],
        model_version=prediction.model_version,
        weather_temp=weather.temperature_c if weather else None,
        weather_humidity=weather.humidity_pct if weather else None,
        weather_rain=weather.precipitation_mm if weather else None,
        weather_risk_level=risk.level if risk.available else None,
        latitude=latitude,
        longitude=longitude,
        region_name=(region_name.strip() if region_name and region_name.strip() else None),
        status=report_status,
    )

    try:
        db.add(report)
        db.commit()
        db.refresh(report)
    except Exception as error:
        db.rollback()
        logger.exception("failed to persist report")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The report could not be saved. Please try again.",
        ) from error

    warnings = list(prediction.warnings)
    if stored.warning:
        warnings.append(stored.warning)
    if not risk.available:
        warnings.append(risk.reason or "Weather risk is unavailable.")

    return schemas.AnalyzeResponse(
        report=schemas.ReportOut.model_validate(report),
        advisory=_advisory_out(prediction.top.label),
        weather=weather.as_dict() if weather else None,
        risk=risk.as_dict(),
        review_required=report_status is ReportStatus.PENDING_EXPERT_REVIEW,
        review_reasons=review_reasons,
        model={
            "backend": prediction.backend,
            "version": prediction.model_version,
            "artifact_type": prediction.artifact_type,
            "inference_ms": prediction.inference_ms,
            "classes": len(service.class_names),
        },
        warnings=warnings,
        disclaimer=advisory_module.disclaimer(),
    )


@app.get("/api/weather", summary="Weather and disease risk for a location")
def get_weather(
    latitude: float = Query(..., ge=-90, le=90),
    longitude: float = Query(..., ge=-180, le=180),
    disease: Optional[str] = Query(
        default=None,
        description="Optional class label; its weather sensitivity refines the score",
    ),
) -> dict:
    """Weather conditions and the deterministic risk band for one point.

    Useful on its own (the outbreak map colours by risk) and as a diagnostic: if
    /api/analyze reports the risk unavailable, this endpoint says why.
    """
    entry = advisory_module.get_advisory(disease) if disease else None
    if disease and entry is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"'{disease}' is not a supported class.",
        )

    snapshot, risk = assess_location(latitude, longitude, entry)
    return {
        "location": {"latitude": latitude, "longitude": longitude},
        "weather": snapshot.as_dict() if snapshot else None,
        "risk": risk.as_dict(),
        "advisory_label": disease,
    }


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------
@app.get("/api/reports", response_model=schemas.ReportListOut, summary="List reports")
def list_reports(
    crop_type: Optional[str] = Query(default=None),
    disease: Optional[str] = Query(default=None),
    risk: Optional[str] = Query(default=None, description="Low, Moderate, High or Critical"),
    status_filter: Optional[str] = Query(default=None, alias="status"),
    region_name: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> schemas.ReportListOut:
    conditions = []
    if crop_type:
        conditions.append(Report.crop_type == _validate_crop(crop_type))
    if disease:
        conditions.append(Report.predicted_disease == disease)
    if risk:
        allowed_risk = {level.value for level in WeatherRiskLevel}
        if risk not in allowed_risk:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Unknown risk level '{risk}'. Use one of: {', '.join(sorted(allowed_risk))}.",
            )
        conditions.append(Report.weather_risk_level == risk)
    if status_filter:
        allowed_status = {value.value for value in ReportStatus}
        if status_filter not in allowed_status:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Unknown status '{status_filter}'. Use one of: "
                    f"{', '.join(sorted(allowed_status))}."
                ),
            )
        conditions.append(Report.status == status_filter)
    if region_name:
        conditions.append(Report.region_name.ilike(f"%{region_name}%"))

    base = select(Report)
    if conditions:
        base = base.where(*conditions)

    total = db.execute(
        select(func.count()).select_from(base.subquery())
    ).scalar_one()

    rows = db.execute(
        base.order_by(Report.created_at.desc(), Report.id.desc()).limit(limit).offset(offset)
    ).scalars().all()

    return schemas.ReportListOut(
        total=int(total),
        limit=limit,
        offset=offset,
        items=[schemas.ReportSummary.model_validate(row) for row in rows],
    )


@app.get("/api/reports/{report_id}", response_model=schemas.ReportOut, summary="One report")
def get_report(report_id: int, db: Session = Depends(get_db)) -> schemas.ReportOut:
    return schemas.ReportOut.model_validate(_load_report(db, report_id))


@app.patch(
    "/api/reports/{report_id}/verify",
    response_model=schemas.ReportOut,
    summary="Expert confirmation or correction",
)
def verify_report(
    report_id: int,
    payload: schemas.VerifyReportRequest,
    db: Session = Depends(get_db),
) -> schemas.ReportOut:
    report = _load_report(db, report_id)

    if report.status in (ReportStatus.EXPERT_CONFIRMED, ReportStatus.EXPERT_CORRECTED):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Report {report_id} was already reviewed "
                f"({report.status.value}). The AI prediction is preserved, but a second "
                "review is not allowed."
            ),
        )

    if payload.action == "confirm":
        report.final_diagnosis = report.predicted_disease
        report.status = ReportStatus.EXPERT_CONFIRMED
    else:
        corrected = payload.corrected_disease or ""
        if not corrected.lower().startswith(f"{report.crop_type.lower()}___"):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"'{corrected}' does not belong to {report.crop_type}. "
                    f"Choose a {report.crop_type} diagnosis."
                ),
            )
        report.final_diagnosis = corrected
        report.status = ReportStatus.EXPERT_CORRECTED

    report.expert_notes = payload.expert_notes
    report.expert_name = payload.expert_name
    report.verified_at = datetime.now(timezone.utc)

    try:
        db.commit()
        db.refresh(report)
    except Exception as error:
        db.rollback()
        logger.exception("failed to save verification for report %s", report_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The verification could not be saved.",
        ) from error

    logger.info(
        "report %s verified: %s by %s", report_id, report.status.value, payload.expert_name
    )
    return schemas.ReportOut.model_validate(report)


@app.post(
    "/api/reports/{report_id}/followup",
    response_model=schemas.FollowUpOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a follow-up observation",
)
def add_followup(
    report_id: int,
    condition_status: FollowUpCondition = Form(...),
    farmer_notes: Optional[str] = Form(default=None),
    image: Optional[UploadFile] = File(default=None),
    db: Session = Depends(get_db),
) -> schemas.FollowUpOut:
    report = _load_report(db, report_id)

    image_url: Optional[str] = None
    warning: Optional[str] = None
    if image is not None and image.filename:
        _validate_content_type(image)
        raw = _read_upload(image)
        if raw:
            try:
                open_image(raw)  # reject unreadable bytes before storing them
            except InvalidImage as error:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)
                ) from error
            stored = storage.store_image(raw, image.filename, image.content_type, folder="cropguard/followups")
            image_url = stored.url
            warning = stored.warning

    if farmer_notes is not None:
        farmer_notes = farmer_notes.strip() or None
    if farmer_notes is not None and len(farmer_notes) > 2000:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="farmer_notes must be 2000 characters or fewer.",
        )

    followup = FollowUp(
        report_id=report.id,
        condition_status=condition_status,
        farmer_notes=farmer_notes,
        followup_image_url=image_url,
    )
    try:
        db.add(followup)
        db.commit()
        db.refresh(followup)
    except Exception as error:
        db.rollback()
        logger.exception("failed to save follow-up for report %s", report_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The follow-up could not be saved.",
        ) from error

    if warning:
        logger.info("follow-up image stored with warning: %s", warning)
    return schemas.FollowUpOut.model_validate(followup)


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------
@app.get("/api/stats", response_model=schemas.StatsResponse, summary="Aggregate statistics")
def get_stats(db: Session = Depends(get_db)) -> schemas.StatsResponse:
    def _key(value) -> str:
        """Enum members come back from the database, so key by their stored value."""
        return value.value if hasattr(value, "value") else str(value)

    status_counts = {
        _key(status_value): count
        for status_value, count in db.execute(
            select(Report.status, func.count()).group_by(Report.status)
        ).all()
    }
    by_crop = {
        crop: count
        for crop, count in db.execute(
            select(Report.crop_type, func.count()).group_by(Report.crop_type)
        ).all()
    }
    by_risk = {
        _key(risk): count
        for risk, count in db.execute(
            select(Report.weather_risk_level, func.count()).group_by(Report.weather_risk_level)
        ).all()
        if risk is not None
    }
    top_diagnoses = [
        {"label": label, "count": count}
        for label, count in db.execute(
            select(Report.predicted_disease, func.count())
            .group_by(Report.predicted_disease)
            .order_by(func.count().desc())
            .limit(5)
        ).all()
    ]

    total = sum(status_counts.values())
    follow_ups_total = int(db.execute(select(func.count()).select_from(FollowUp)).scalar_one())
    since = datetime.now(timezone.utc) - timedelta(days=7)
    last_7_days = int(
        db.execute(
            select(func.count()).select_from(Report).where(Report.created_at >= since)
        ).scalar_one()
    )

    return schemas.StatsResponse(
        total_reports=total,
        pending_review=status_counts.get(ReportStatus.PENDING_EXPERT_REVIEW.value, 0),
        ai_verified=status_counts.get(ReportStatus.AI_VERIFIED.value, 0),
        expert_confirmed=status_counts.get(ReportStatus.EXPERT_CONFIRMED.value, 0),
        expert_corrected=status_counts.get(ReportStatus.EXPERT_CORRECTED.value, 0),
        follow_ups_total=follow_ups_total,
        reports_last_7_days=last_7_days,
        by_crop=by_crop,
        by_risk=by_risk,
        top_diagnoses=top_diagnoses,
    )
