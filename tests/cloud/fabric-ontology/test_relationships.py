import pytest
from eda_worker.acceptance.fabric_ontology import OntologyCloudObservations

pytestmark = pytest.mark.cloud


def test_relationship_bindings_match_fixture(ontology_observations: OntologyCloudObservations) -> None:
    assert len(ontology_observations.relationship_sha256) == 64
    assert ontology_observations.controls["relationships"] == "passed"
