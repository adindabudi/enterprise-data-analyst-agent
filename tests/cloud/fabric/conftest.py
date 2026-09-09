from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from .evidence import FabricCloudObservations


@pytest.fixture(scope="session")
def fabric_observations() -> FabricCloudObservations:
    if os.getenv("EDA_RUN_FABRIC_ACCEPTANCE") != "1":
        pytest.skip("EDA_RUN_FABRIC_ACCEPTANCE=1 is required for Fabric cloud acceptance")
    path_value = os.getenv("EDA_FABRIC_ACCEPTANCE_OBSERVATIONS")
    if not path_value:
        pytest.fail("EDA_FABRIC_ACCEPTANCE_OBSERVATIONS is required")
    path = Path(path_value)
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        pytest.fail("Fabric acceptance observations must have mode 0600")
    return FabricCloudObservations.model_validate_json(path.read_text(encoding="utf-8"))
