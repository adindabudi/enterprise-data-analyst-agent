from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
from eda_worker.acceptance.fabric_ontology import OntologyCloudObservations


@pytest.fixture(scope="session")
def ontology_observations() -> OntologyCloudObservations:
    if os.getenv("EDA_RUN_FABRIC_ONTOLOGY_ACCEPTANCE") != "1":
        pytest.skip("EDA_RUN_FABRIC_ONTOLOGY_ACCEPTANCE=1 is required")
    path_value = os.getenv("EDA_FABRIC_ONTOLOGY_OBSERVATIONS")
    if not path_value:
        pytest.fail("EDA_FABRIC_ONTOLOGY_OBSERVATIONS is required")
    path = Path(path_value)
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        pytest.fail("ontology observations must have mode 0600")
    return OntologyCloudObservations.model_validate_json(path.read_text(encoding="utf-8"))
