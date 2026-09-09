import pytest
from eda_worker.acceptance.fabric_ontology import OntologyCloudObservations

pytestmark = pytest.mark.cloud


def test_denied_user_receives_no_data_or_artifact(ontology_observations: OntologyCloudObservations) -> None:
    assert ontology_observations.denied_error_code == "authorization_denied"
    assert ontology_observations.controls["permission_denial"] == "passed"
