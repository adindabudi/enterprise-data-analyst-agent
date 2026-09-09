import pytest
from eda_worker.acceptance.fabric_ontology import OntologyCloudObservations

pytestmark = pytest.mark.cloud


def test_cross_tenant_owner_bound_login(ontology_observations: OntologyCloudObservations) -> None:
    assert len(set(ontology_observations.tenant_hashes)) == 2
    assert ontology_observations.controls["cross_tenant_login"] == "passed"
