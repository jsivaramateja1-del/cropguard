"""Advisory knowledge base loader.

The knowledge base lives in ``model/advisory_db.json`` and carries one entry per
supported class — the same 21 labels as ``ml_training/class_names_canonical.json``.
Keeping the loader here means the API, the schemas and the tests all agree on one
definition of "supported disease".

Nothing in this module invents pesticide advice: it only serves what the JSON
contains, and the JSON deliberately carries active-ingredient *groups* and safety
warnings rather than dosage figures.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional

from config import BASE_DIR

ADVISORY_PATH = BASE_DIR / "model" / "advisory_db.json"

REQUIRED_ENTRY_KEYS = (
    "crop",
    "condition",
    "is_healthy",
    "summary",
    "symptoms",
    "cultural_control",
    "organic_management",
    "chemical_management",
    "monitoring",
)


class AdvisoryError(RuntimeError):
    """Raised when the knowledge base is missing or malformed."""


@lru_cache(maxsize=1)
def load_advisory_db(path: Optional[str] = None) -> dict:
    """Load and structurally validate the knowledge base once per process."""
    resolved = Path(path) if path else ADVISORY_PATH
    if not resolved.is_file():
        raise AdvisoryError(f"Advisory knowledge base not found: {resolved}")

    with resolved.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    entries = payload.get("entries")
    if not isinstance(entries, dict) or not entries:
        raise AdvisoryError(f"{resolved} has no 'entries' object")

    problems: List[str] = []
    for label, entry in entries.items():
        missing = [key for key in REQUIRED_ENTRY_KEYS if key not in entry]
        if missing:
            problems.append(f"{label}: missing {', '.join(missing)}")
            continue
        chemical = entry["chemical_management"]
        if not isinstance(chemical, dict) or "recommended" not in chemical:
            problems.append(f"{label}: chemical_management must be an object with 'recommended'")
        if entry["is_healthy"] is False and not entry["cultural_control"]:
            problems.append(f"{label}: diseased entry has no cultural_control steps")
    if problems:
        raise AdvisoryError("Advisory knowledge base is invalid:\n  " + "\n  ".join(problems))

    return payload


def supported_diseases(path: Optional[str] = None) -> List[str]:
    """Every label the knowledge base can advise on, in file order."""
    return list(load_advisory_db(path)["entries"].keys())


def get_advisory(label: str, path: Optional[str] = None) -> Optional[dict]:
    """Advisory entry for a label, or None when the label is not supported."""
    return load_advisory_db(path)["entries"].get(label)


def advisory_for_crop(crop: str, path: Optional[str] = None) -> Dict[str, dict]:
    """All entries belonging to one crop, keyed by label."""
    entries = load_advisory_db(path)["entries"]
    return {
        label: entry
        for label, entry in entries.items()
        if entry.get("crop", "").lower() == crop.lower()
    }


def disclaimer(path: Optional[str] = None) -> str:
    """The safety notice the API attaches to every advisory it returns."""
    return load_advisory_db(path).get("disclaimer", "")
