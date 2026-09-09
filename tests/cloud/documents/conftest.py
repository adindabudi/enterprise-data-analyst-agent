from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from .evidence import DocumentAcceptanceObservations


@pytest.fixture(scope="session")
def document_observations() -> DocumentAcceptanceObservations:
    if os.getenv("EDA_RUN_DOCUMENT_ACCEPTANCE") != "1":
        pytest.skip("EDA_RUN_DOCUMENT_ACCEPTANCE=1 is required for Document Pack cloud acceptance")
    path_value = os.getenv("EDA_DOCUMENT_ACCEPTANCE_OBSERVATIONS")
    if not path_value:
        pytest.fail("EDA_DOCUMENT_ACCEPTANCE_OBSERVATIONS is required")
    path = Path(path_value)
    if not path.is_file() or stat.S_IMODE(path.stat().st_mode) != 0o600:
        pytest.fail("Document Pack acceptance observations must be a mode-0600 regular file")
    return DocumentAcceptanceObservations.model_validate_json(path.read_text(encoding="utf-8"))
