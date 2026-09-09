import pytest
from eda_worker.acceptance.fabric_ontology import OntologyCloudObservations

pytestmark = pytest.mark.cloud


def test_vs1001_time_series_controls_match(ontology_observations: OntologyCloudObservations) -> None:
    assert len(ontology_observations.time_series_controls_sha256) == 64
    assert ontology_observations.controls["time_series_controls"] == "passed"
