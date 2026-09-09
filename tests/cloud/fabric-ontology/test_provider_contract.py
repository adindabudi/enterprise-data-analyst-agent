import pytest
from eda_worker.acceptance.fabric_ontology import OntologyCloudObservations

pytestmark = pytest.mark.cloud


def test_ontology_provider_contract_and_fixture_are_bound(ontology_observations: OntologyCloudObservations) -> None:
    assert len(ontology_observations.provider_contract_sha256) == 64
    assert ontology_observations.fixture_sha256 == "10e9f03a7361cdbdea5feac216b6762a5b7b22378a15893a0167c96b54dd9bf2"
    assert ontology_observations.controls["provider_contract"] == "passed"
