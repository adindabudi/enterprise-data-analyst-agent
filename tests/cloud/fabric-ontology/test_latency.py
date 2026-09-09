import pytest
from eda_worker.acceptance.fabric_ontology import OntologyCloudObservations

pytestmark = pytest.mark.cloud


def test_cold_and_warm_profile_specific_latency_samples_are_bounded(
    ontology_observations: OntologyCloudObservations,
) -> None:
    assert len(ontology_observations.cold_latency_ms) == 5
    assert len(ontology_observations.warm_latency_ms) == 5
    assert all(
        0 <= value <= 120_000
        for value in (*ontology_observations.cold_latency_ms, *ontology_observations.warm_latency_ms)
    )
    assert ontology_observations.controls["latency"] == "passed"
