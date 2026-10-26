"""Application configuration.

Reads environment variables (and backend/.env when present) into a single typed
object. Nothing here holds a secret default: every credential starts empty and the
code degrades to a documented development fallback when it is missing.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import List, Optional

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent


def glob_to_regex(pattern: str) -> str:
    """Turn an origin with a single ``*`` into an anchored regex.

    ``https://*.vercel.app`` becomes ``https://[^./]+\\.vercel\\.app``. The wildcard
    matches one host label only, so a lookalike such as ``https://vercel.app.evil.com``
    or ``https://a.b.vercel.app`` cannot slip through.
    """
    escaped = re.escape(pattern)
    return escaped.replace(r"\*", r"[^./]+")
ENV_FILE = BASE_DIR / ".env"


class Settings(BaseSettings):
    """Typed view of the environment. See .env.example for documentation."""

    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Database: empty -> local SQLite
    database_url: str = ""

    # Supabase (reserved for authenticated flows beyond the prototype)
    supabase_url: str = ""
    supabase_anon_key: str = ""

    # Image storage: all three set -> Cloudinary, otherwise local disk
    cloudinary_cloud_name: str = ""
    cloudinary_api_key: str = ""
    cloudinary_api_secret: str = ""

    # Model artifacts, resolved relative to backend/
    model_path: str = "model/cropguard_mobilenetv3.pth"
    onnx_path: str = "model/cropguard_mobilenetv3.onnx"
    class_names_path: str = "model/class_names.json"
    model_backend: str = "auto"  # auto | onnx | torch

    # Weather (Open-Meteo). No key is required for non-commercial use, but the free
    # endpoint is rate-limited per IP and cloud hosts share egress IPs — an API key
    # moves requests to the customer endpoint and avoids HTTP 429 entirely.
    open_meteo_api_key: str = ""
    weather_cache_ttl: int = 600
    weather_timeout: float = 8.0
    # auto = try Open-Meteo then MET Norway; or pin one provider.
    weather_provider: str = "auto"  # auto | open-meteo | met-no
    # MET Norway requires an identifying User-Agent with real contact details.
    weather_contact: str = "cropguard-demo"

    # Decision thresholds (see docs/calibration.md before changing)
    confidence_threshold: float = 0.75
    # Below this, a prediction is outside the range that genuine leaf photos produce.
    # Measured on the validation set: median 0.907, 5th percentile 0.880. A photo of
    # something that is not a crop leaf typically scores lower, so this catches cases a
    # colour check cannot — a bird on a branch, for example, which scored 0.81.
    ood_confidence_floor: float = 0.88

    # Uploads
    max_upload_mb: int = 8
    min_image_pixels: int = 96
    max_image_pixels: int = 6000

    # --- account authentication ------------------------------------------------
    # Signing key for access tokens. If unset, a random key is generated per process,
    # which invalidates tokens on restart — safe, but inconvenient, so set it.
    # Testing escape hatch: keep images on disk even when the database could hold them.
    force_local_storage: bool = False

    secret_key: str = ""
    access_token_ttl_seconds: int = 86400
    login_max_failures: int = 8
    login_failure_window: int = 900

    # --- staff authentication (legacy shared secret) --------------------------
    # Prototype-grade identity: one shared secret per role, supplied as a bearer token.
    # No default is provided, so an unconfigured deployment has no privileged access at
    # all rather than a default password. See docs/security.md for the upgrade path.
    expert_token: str = ""
    expert_name: str = "Agricultural expert"
    admin_token: str = ""
    admin_name: str = "Administrator"

    # Costly analysis calls allowed per client per window.
    analyze_rate_limit: int = 20
    analyze_rate_window: int = 60

    # API
    # Comma-separated browser origins. An entry may contain a single wildcard label,
    # e.g. https://*.vercel.app, which is matched by regex instead of exactly.
    frontend_origin: str = "http://localhost:5173"
    # Escape hatch for an arbitrary pattern; combined with any wildcard entries above.
    frontend_origin_regex: str = ""
    log_level: str = "INFO"

    # ------------------------------------------------------------------ helpers
    @property
    def _origin_entries(self) -> List[str]:
        return [entry.strip() for entry in self.frontend_origin.split(",") if entry.strip()]

    @property
    def cors_origins(self) -> List[str]:
        """Exact origins only. Wildcard entries are handled by cors_origin_regex."""
        return [entry for entry in self._origin_entries if "*" not in entry]

    @property
    def cors_origin_regex(self) -> Optional[str]:
        """Regex matching any wildcard origin entries.

        Vercel gives every deployment its own hostname (project-hash-team.vercel.app),
        so an exact allow-list breaks on each deploy. A pattern survives that.
        """
        patterns = [glob_to_regex(entry) for entry in self._origin_entries if "*" in entry]
        if self.frontend_origin_regex.strip():
            patterns.append(self.frontend_origin_regex.strip())
        if not patterns:
            return None
        return "|".join(f"(?:{pattern})" for pattern in patterns)

    def _resolve(self, value: str) -> Path:
        path = Path(value)
        return path if path.is_absolute() else (BASE_DIR / path).resolve()

    @property
    def model_path_resolved(self) -> Path:
        return self._resolve(self.model_path)

    @property
    def onnx_path_resolved(self) -> Path:
        return self._resolve(self.onnx_path)

    @property
    def class_names_path_resolved(self) -> Path:
        return self._resolve(self.class_names_path)

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def weather_user_agent(self) -> str:
        return f"CropGuard/{0.3} (agricultural decision support; {self.weather_contact})"

    @property
    def cloudinary_enabled(self) -> bool:
        return bool(
            self.cloudinary_cloud_name
            and self.cloudinary_api_key
            and self.cloudinary_api_secret
        )

    @property
    def uploads_dir(self) -> Path:
        return BASE_DIR / "uploads"

    @property
    def is_production(self) -> bool:
        return os.getenv("APP_ENV", "development").lower() in {"production", "prod"}


settings = Settings()
