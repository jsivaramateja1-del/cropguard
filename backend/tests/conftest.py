"""Test bootstrap.

Sets DATABASE_URL to a throwaway SQLite file *before* ``database`` is imported, so
tests never touch the developer's cropguard_local.db, and puts ``backend/`` on the
import path so modules can be imported the same way uvicorn imports them.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

_TMP_DB = Path(tempfile.mkdtemp(prefix="cropguard-tests-")) / "test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP_DB}"
os.environ.setdefault("FRONTEND_ORIGIN", "http://localhost:5173")
