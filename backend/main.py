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
from typing import Dict, List, Optional, Tuple

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

import advisory as advisory_module
import schemas
import storage
import database
import security
from config import settings
from database import database_info, get_db, init_db, try_init_db
from inference import (
    InvalidImage,
    ModelUnavailable,
    get_model_service,
    leaf_plausibility,
    open_image,
)
from models import (
    AuditEvent,
    CROP_TYPES,
    FollowUp,
    FollowUpCondition,
    Report,
    ReportStatus,
)
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
    # A database that cannot be reached must not stop the API from starting: /health and
    # the storage-free endpoints still work, and the reason is logged instead of being
    # hidden behind a process exit.
    if try_init_db() is None:
        logger.info("database ready: %s", database_info())
    else:
        logger.warning("starting with the database unavailable; storage endpoints will return 503")

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


ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/jpg", "image/png", "image/webp"}
ALLOWED_IMAGE_FORMATS = {"JPEG", "PNG", "WEBP"}


def _validate_content_type(upload: UploadFile) -> None:
    """Reject a declared type we do not accept.

    This is only the cheap first gate: the declared Content-Type is supplied by the
    client and proves nothing. The real check is decoding the bytes with PIL and looking
    at the format it actually detected — see ``_validate_decoded_format``.
    """
    declared = (upload.content_type or "").lower().split(";")[0].strip()
    if declared and declared not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=(
                f"'{declared}' is not an accepted image type. Send a JPEG, PNG or WebP."
            ),
        )


def _validate_decoded_format(image) -> None:
    """Reject bytes that decode to something other than a still image format.

    A file can be named ``leaf.jpg`` and declare ``image/jpeg`` while actually being a
    GIF, a TIFF or a polyglot. What matters is what the decoder found.
    """
    detected = (getattr(image, "format", None) or "").upper()
    if detected and detected not in ALLOWED_IMAGE_FORMATS:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=(
                f"The file is actually {detected}, which is not accepted. "
                "Send a JPEG, PNG or WebP."
            ),
        )


def _crop_of(label: str) -> str:
    """Local copy of the label convention: 'Crop___Condition'."""
    return label.split("___")[0] if "___" in label else label


def _model_supported_crops() -> List[str]:
    """Crops the *deployed model* can actually classify, not the ones we list in the UI.

    A crop name appearing in a frontend dropdown is not evidence that the model can
    diagnose it. Wheat is the live example: it is a supported crop concept with advisory
    content, but the trained artifact has no wheat classes, so a wheat photo would
    silently return a tomato or potato diagnosis.
    """
    service = get_model_service()
    if not service.available:
        return []
    crops = sorted({_crop_of(label) for label in service.class_names})
    return crops


def _classes_by_crop() -> Dict[str, List[str]]:
    service = get_model_service()
    if not service.available:
        return {}
    grouped: Dict[str, List[str]] = {}
    for label in service.class_names:
        grouped.setdefault(_crop_of(label), []).append(label)
    return {crop: sorted(labels) for crop, labels in sorted(grouped.items())}


def _validate_crop(crop: str) -> str:
    """Validate against the model's real capability, and say why when it fails."""
    known = {name.lower(): name for name in CROP_TYPES}
    if crop.lower() not in known:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Unknown crop '{crop}'. CropGuard recognises: {', '.join(CROP_TYPES)}."
            ),
        )
    canonical = known[crop.lower()]

    supported = _model_supported_crops()
    if supported and canonical not in supported:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"{canonical} is not supported by the deployed model, which was trained "
                f"on {', '.join(supported)}. Submitting a {canonical} photo would return "
                "a diagnosis for a different crop, so the request is refused. Choose one "
                "of the supported crops, or ask for a model that covers "
                f"{canonical}."
            ),
        )
    return canonical


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


def _set_reference(db: Session, report: Report) -> str:
    """Human-facing reference, derived from the id once it exists.

    Deliberately public: it identifies a report for a farmer reading it aloud, and grants
    nothing. Access is by token, never by this number.
    """
    reference = f"CG-{report.id:05d}"
    report.reference = reference
    db.flush()
    return reference


def _authorise_report(
    report: Report,
    report_token: Optional[str],
    authorization: Optional[str],
) -> Optional[Tuple[str, str]]:
    """Allow the report's owner, or any authenticated staff member.

    Returns the staff identity when a staff token was used, else None. A report id alone
    never authorises anything, and a wrong token is reported identically to a missing one
    so the response cannot be used to probe which ids exist.
    """
    staff = security.staff_identity(authorization)
    if staff is not None:
        return staff
    if security.token_matches(report_token, report.owner_token_hash):
        return None
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=(
            "Report not found. Access needs the access token returned when the report was "
            "created."
        ),
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
    plausible_leaf: bool = True,
    vegetation_fraction: float = 1.0,
    confidence_floor: float = 0.88,
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
    if not plausible_leaf:
        reasons.append(
            f"The photo does not look like crop foliage — only "
            f"{vegetation_fraction:.0%} of it is green or brown plant material. "
            "The model must choose one of its 16 known classes, so this result is "
            "unreliable."
        )
    if confidence < 0.35:
        reasons.append(
            "No class reached a plausible confidence. The photo may be outside the 16 "
            "diseases this model knows."
        )
    elif confidence < confidence_floor:
        reasons.append(
            f"Confidence {confidence:.2f} is below the {confidence_floor:.2f} that genuine "
            "leaf photos normally reach on this model, so the photo may not be a crop leaf."
        )

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
            "GET /api/outbreaks",
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
    """Component-level readiness, with nothing sensitive in it.

    Two rules this follows deliberately:

    * The diagnosis model is **essential**. If it cannot load, the service is not healthy,
      whatever else works — a health check that says "ok" while every analysis fails is
      worse than no health check.
    * No filesystem paths, database hosts, credentials or stack traces. Operational detail
      belongs in the logs, which are access-controlled; a public health endpoint is not.
    """
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
        db_error = f"{type(error).__name__}"

    storage_status = storage.storage_status()
    weather_status = weather_risk.weather_status()

    if not db_ok or not model["available"]:
        overall = "unhealthy"
    elif not storage_status.get("writable", True):
        overall = "degraded"
    else:
        overall = "ok"

    return {
        "status": overall,
        "version": API_VERSION,
        "checks": {
            "database": {
                "ok": db_ok and database.INIT_ERROR is None,
                "dialect": database_info()["dialect"],
                # Whether the schema was prepared at startup. A reachable database whose
                # migrations never ran is still not usable.
                "initialised": database.INIT_ERROR is None,
                "error": db_error or database.INIT_ERROR,
            },
            "model": {
                "ok": bool(model["available"]),
                "backend": model["backend"],
                "classes": model["classes"],
                "artifact_type": model["artifact_type"],
                "error": model["error"],
            },
            "storage": {
                "backend": storage_status.get("backend"),
                "writable": storage_status.get("writable"),
            },
            "weather": {
                "providers": weather_status.get("provider_order"),
                "cached_locations": weather_status.get("cache_entries"),
                "api_key_configured": weather_status.get("api_key_configured"),
            },
            "auth": {
                "staff_configured": security.staff_auth_configured(),
            },
        },
        "thresholds": {
            "confidence": settings.confidence_threshold,
            "ood_floor": settings.ood_confidence_floor,
        },
        "advisory_entries": len(advisory_module.supported_diseases()),
    }


@app.get("/api/model", response_model=schemas.ModelContractOut, summary="Deployed model contract")
def model_contract() -> schemas.ModelContractOut:
    """The single verified description of what this deployment can and cannot do.

    The frontend reads this instead of assuming that a crop listed in a dropdown is
    supported. It is public because none of it is sensitive: it is the model's own
    metadata, and a farmer has a right to know what the model was trained on.
    """
    service = get_model_service()
    describe = service.describe()
    if not service.available:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"The diagnosis model is not available: {describe['error']}",
        )

    classes = list(service.class_names)
    by_crop = _classes_by_crop()
    return schemas.ModelContractOut(
        model_version=describe["model_version"],
        artifact_type=describe["artifact_type"],
        backend=describe["backend"],
        classes=classes,
        output_dimensions=len(classes),
        image_size=describe["image_size"],
        channel_order="RGB",
        normalization={
            "mean": [0.485, 0.456, 0.406],
            "std": [0.229, 0.224, 0.225],
        },
        supported_crops=sorted(by_crop),
        classes_by_crop=by_crop,
        healthy_class_available={
            crop: any(label.lower().endswith("healthy") for label in labels)
            for crop, labels in by_crop.items()
        },
        confidence_threshold=settings.confidence_threshold,
        ood_confidence_floor=settings.ood_confidence_floor,
        provenance={
            "artifact": "cropguard_mobilenetv3",
            "trained_on": "PlantVillage colour (tomato, potato) + Rice Leaf Diseases",
            "note": (
                "Label order comes from class_names.json and is validated against the "
                "graph's output width at load time."
            ),
        },
    )


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
    request: Request = None,
    db: Session = Depends(get_db),
) -> schemas.AnalyzeResponse:
    crop = _validate_crop(crop_type)

    allowed, retry_after = security.analyze_limiter().allow(security.client_key(request))
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many analyses from this device. Wait a moment and try again.",
            headers={"Retry-After": str(retry_after)},
        )
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

    _validate_decoded_format(pil_image)
    plausibility = leaf_plausibility(pil_image)

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
        plausible_leaf=bool(plausibility["is_plausible_leaf"]),
        vegetation_fraction=float(plausibility["vegetation_fraction"]),
        confidence_floor=settings.ood_confidence_floor,
    )

    # One-time farmer access token: returned once, stored only as a hash.
    access_token = security.new_report_token()

    report = Report(
        farmer_name=farmer_name.strip(),
        owner_token_hash=security.hash_token(access_token),
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
        _set_reference(db, report)
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
    if not plausibility["is_plausible_leaf"]:
        warnings.append(
            "This photo may not be a crop leaf. The model can only choose from its 16 "
            "trained diseases, so treat the diagnosis above with suspicion and submit a "
            "clear photo of an affected leaf."
        )
    if stored.warning:
        warnings.append(stored.warning)
    if not risk.available:
        warnings.append(risk.reason or "Weather risk is unavailable.")

    # Crop-specific advice is withheld when the photo does not match the selected crop:
    # recommending a tomato treatment for something the model called potato would be
    # actively harmful, and is exactly what the review queue exists to catch.
    crop_matches = prediction.top.crop == crop
    advisory_payload = _advisory_out(prediction.top.label) if crop_matches else None
    if not crop_matches:
        warnings.append(
            f"The photo looks like {prediction.top.crop}, not {crop}. Crop-specific "
            "advice is withheld until the mismatch is resolved; general guidance for the "
            f"predicted crop ({prediction.top.condition}) is available after expert review."
        )
    if crop == "Rice" and "Rice___healthy" not in (service.class_names or []):
        warnings.append(
            "The deployed model has no healthy-rice class, so it cannot confirm that a "
            "rice leaf is healthy. Any rice result should be treated as a disease "
            "screening only."
        )

    return schemas.AnalyzeResponse(
        report=schemas.ReportOut.model_validate(report),
        access_token=access_token,
        plausibility=schemas.PlausibilityOut(**plausibility),
        advisory=advisory_payload,
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
    staff: Tuple[str, str] = Depends(security.require_staff),
    db: Session = Depends(get_db),
) -> schemas.ReportListOut:
    """The review queue. Staff only — it contains every farmer's name and GPS position."""
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
def get_report(
    report_id: int,
    x_report_token: Optional[str] = Header(default=None, alias="X-Report-Token"),
    authorization: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
) -> schemas.ReportOut:
    """One report, for its owner (by access token) or for staff."""
    report = _load_report(db, report_id)
    _authorise_report(report, x_report_token, authorization)
    return schemas.ReportOut.model_validate(report)


@app.patch(
    "/api/reports/{report_id}/verify",
    response_model=schemas.ReportOut,
    summary="Expert confirmation or correction",
)
def verify_report(
    report_id: int,
    payload: schemas.VerifyReportRequest,
    staff: Tuple[str, str] = Depends(security.require_expert),
    db: Session = Depends(get_db),
) -> schemas.ReportOut:
    """Expert confirmation or correction. Requires a validated expert token.

    The expert name in the body is recorded as a note, never as authorisation: the
    identity comes from the bearer token.
    """
    actor_role, actor_name = staff
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

    # Captured before anything changes: the audit row must say what the state *was*.
    previous_state = f"{report.status.value}:{report.effective_diagnosis}"[:120]

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
    # Recorded as a note; authorisation came from the bearer token, not this field.
    report.expert_name = payload.expert_name
    report.verified_at = datetime.now(timezone.utc)

    try:
        db.add(
            AuditEvent(
                report_id=report.id,
                action="verify",
                actor_role=actor_role,
                actor_name=actor_name,
                previous_state=previous_state,
                new_state=f"{report.status.value}:{report.effective_diagnosis}"[:120],
                notes=payload.expert_notes,
            )
        )
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
    x_report_token: Optional[str] = Header(default=None, alias="X-Report-Token"),
    authorization: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
) -> schemas.FollowUpOut:
    """A follow-up observation: the report's owner, or staff."""
    report = _load_report(db, report_id)
    _authorise_report(report, x_report_token, authorization)

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
@app.get("/api/outbreaks", response_model=schemas.OutbreakResponse, summary="Public outbreak map")
def get_outbreaks(
    crop_type: Optional[str] = Query(default=None),
    disease: Optional[str] = Query(default=None),
    risk: Optional[str] = Query(default=None, description="Low, Moderate, High or Critical"),
    region_name: Optional[str] = Query(default=None),
    days: int = Query(default=90, ge=1, le=730, description="How far back to look"),
    db: Session = Depends(get_db),
) -> schemas.OutbreakResponse:
    """Anonymised outbreak points for the public map.

    Separate from `GET /api/reports` on purpose. The reports list is a review queue: it
    contains farmer names, exact GPS and image URLs, so it is staff-only. This endpoint
    answers a different question — *where is disease being reported* — and returns only
    what that question needs:

    * coordinates rounded to 2 decimal places (~1 km), so a farm is not pinpointed
    * no farmer name, image, expert notes or access token
    * identical (location, crop, disease) reports collapsed into one point with a count

    Aggregation happens in Python rather than SQL so the rounding and grouping behave
    identically on SQLite and PostgreSQL.
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)
    query = select(Report).where(Report.created_at >= since)
    if crop_type:
        query = query.where(Report.crop_type == crop_type)
    if disease:
        query = query.where(Report.predicted_disease == disease)
    if risk:
        query = query.where(Report.weather_risk_level == risk)
    if region_name:
        query = query.where(Report.region_name.ilike(f"%{region_name}%"))

    rows = db.execute(query.order_by(Report.created_at.desc()).limit(5000)).scalars().all()

    buckets: Dict[tuple, dict] = {}
    by_crop: Dict[str, int] = {}
    by_risk: Dict[str, int] = {}
    by_disease: Dict[str, int] = {}
    hotspots: Dict[tuple, dict] = {}

    for report in rows:
        latitude = round(float(report.latitude), 2)
        longitude = round(float(report.longitude), 2)
        risk_level = report.weather_risk_level.value if report.weather_risk_level else None

        key = (latitude, longitude, report.crop_type, report.predicted_disease, risk_level)
        entry = buckets.get(key)
        if entry is None:
            buckets[key] = {
                "latitude": latitude,
                "longitude": longitude,
                "crop_type": report.crop_type,
                "predicted_disease": report.predicted_disease,
                "weather_risk_level": risk_level,
                "status": report.status.value,
                "reports": 1,
                "latest_at": report.created_at.isoformat() if report.created_at else "",
            }
        else:
            entry["reports"] += 1

        by_crop[report.crop_type] = by_crop.get(report.crop_type, 0) + 1
        if risk_level:
            by_risk[risk_level] = by_risk.get(risk_level, 0) + 1
        by_disease[report.predicted_disease] = by_disease.get(report.predicted_disease, 0) + 1

        hot_key = (latitude, longitude)
        hotspot = hotspots.get(hot_key)
        if hotspot is None:
            hotspots[hot_key] = {
                "latitude": latitude,
                "longitude": longitude,
                "region_name": report.region_name,
                "reports": 1,
            }
        else:
            hotspot["reports"] += 1

    top_hotspots = sorted(hotspots.values(), key=lambda item: -item["reports"])[:10]

    return schemas.OutbreakResponse(
        points=[schemas.OutbreakPointOut(**point) for point in buckets.values()],
        summary=schemas.OutbreakSummaryOut(
            total_reports=len(rows),
            points=len(buckets),
            period_days=days,
            by_crop=by_crop,
            by_risk=by_risk,
            by_disease=dict(sorted(by_disease.items(), key=lambda item: -item[1])[:10]),
            hotspots=[schemas.OutbreakHotspotOut(**spot) for spot in top_hotspots],
        ),
        attribution="Weather data by Open-Meteo.com (CC BY 4.0). Coordinates rounded to ~1 km.",
    )


@app.get("/api/stats", response_model=schemas.StatsResponse, summary="Aggregate statistics")
def get_stats(
    staff: Tuple[str, str] = Depends(security.require_staff),
    db: Session = Depends(get_db),
) -> schemas.StatsResponse:
    """Operational aggregates. Staff only."""
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
