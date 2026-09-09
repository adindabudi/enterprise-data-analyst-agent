import pytest
from eda_worker.acceptance.fabric_ontology import OntologyCloudObservations

pytestmark = pytest.mark.cloud


def test_profile_bound_english_and_indonesian_routing(ontology_observations: OntologyCloudObservations) -> None:
    assert ontology_observations.model_profile == "gpt-5.6-terra-medium-v1"
    assert ontology_observations.served_model == "gpt-5.6-terra"
    assert ontology_observations.served_snapshot == "2026-07-09"
    assert ontology_observations.controls["model_routing"] == "passed"
