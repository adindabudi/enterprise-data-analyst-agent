import pytest
from eda_worker.acceptance.fabric_ontology import OntologyCloudObservations

pytestmark = pytest.mark.cloud


def test_static_lamna_controls_match(ontology_observations: OntologyCloudObservations) -> None:
    assert len(ontology_observations.static_controls_sha256) == 64
    assert ontology_observations.controls["static_controls"] == "passed"
