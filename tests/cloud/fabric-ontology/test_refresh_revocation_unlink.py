import pytest
from eda_worker.acceptance.fabric_ontology import OntologyCloudObservations

pytestmark = pytest.mark.cloud


def test_refresh_revocation_unlink_and_relink(ontology_observations: OntologyCloudObservations) -> None:
    assert ontology_observations.refresh_revocation_unlink == "passed"
    assert ontology_observations.controls["refresh_revocation_unlink"] == "passed"
