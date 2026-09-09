import pytest
from eda_worker.acceptance.fabric_ontology import OntologyCloudObservations

pytestmark = pytest.mark.cloud


def test_ontology_surfaces_have_no_secret_or_topology_markers(ontology_observations: OntologyCloudObservations) -> None:
    assert ontology_observations.forbidden_markers_found == 0
    assert ontology_observations.fallback_paths_observed == ()
    assert ontology_observations.controls["no_secrets"] == "passed"
    assert ontology_observations.controls["no_fallback"] == "passed"
