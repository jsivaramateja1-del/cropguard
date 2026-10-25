"""Application configuration.

Reads environment variables (and backend/.env when present) into a single typed
object. Nothing here holds a secret default: every credential starts empty and the
code degrades to a documented development fallback when it is missing.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent
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

    # API
    frontend_origin: str = "http://localhost:5173"
    log_level: str = "INFO"

    # ------------------------------------------------------------------ helpers
    @property
    def cors_origins(self) -> List[str]:
        """FRONTEND_ORIGIN may list several comma-separated origins."""
        origins = [origin.strip() for origin in self.frontend_origin.split(",")]
        return [origin for origin in origins if origin]

    @property
    def model_path_resolved(self) -> Path:
        path = Path(self.model_path)
        return path if path.is_absolute() else (BASE_DIR / path).resolve()

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
