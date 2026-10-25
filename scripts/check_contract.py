#!/usr/bin/env python3
"""Repository consistency checks — run by CI and usable locally.

Verifies two things that a test suite inside one component cannot see:

1. The model's class list (ml_training/class_names_canonical.json) and the
   advisory knowledge base (backend/model/advisory_db.json) describe exactly the
   same diseases. If they drift, the backend can serve a prediction it has no
   advice for, or carry advice for a class the model cannot predict.
2. Every tracked .json file parses. A stray comma in a knowledge base is an
   outage, not a warning.

Exit code is non-zero on any failure, so CI fails loudly.

Usage:
    python scripts/check_contract.py
"""

from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
CANONICAL = ROOT / "ml_training" / "class_names_canonical.json"
ADVISORY = ROOT / "backend" / "model" / "advisory_db.json"
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "dist", "build"}


def check_contract() -> list[str]:
    problems: list[str] = []

    for path in (CANONICAL, ADVISORY):
        if not path.is_file():
            problems.append(f"missing file: {path.relative_to(ROOT)}")
    if problems:
        return problems

    canonical = json.loads(CANONICAL.read_text(encoding="utf-8")).get("classes", [])
    entries = json.loads(ADVISORY.read_text(encoding="utf-8")).get("entries", {})

    print(f"canonical classes : {len(canonical)}")
    print(f"advisory entries  : {len(entries)}")

    missing = sorted(set(canonical) - set(entries))
    extra = sorted(set(entries) - set(canonical))
    if missing:
        problems.append("classes with no advisory entry: " + ", ".join(missing))
    if extra:
        problems.append("advisory entries with no model class: " + ", ".join(extra))

    for label, entry in entries.items():
        crop = entry.get("crop")
        if not crop:
            problems.append(f"{label}: entry has no crop")
        elif not label.lower().startswith(f"{crop.lower()}___"):
            problems.append(f"{label}: label does not start with its crop '{crop}'")

    return problems


def check_json_files() -> list[str]:
    problems: list[str] = []
    checked = 0
    for path in sorted(ROOT.rglob("*.json")):
        if any(part in SKIP_DIRS for part in path.relative_to(ROOT).parts):
            continue
        checked += 1
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            problems.append(f"{path.relative_to(ROOT)}: {error}")
    print(f"json files checked: {checked}")
    return problems


def main() -> int:
    print("== class / advisory contract ==")
    problems = check_contract()
    print("== json syntax ==")
    problems += check_json_files()

    if problems:
        print("\nFAILED:")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    print("\nOK — class list and advisory knowledge base are in sync, all json parses")
    return 0


if __name__ == "__main__":
    sys.exit(main())
