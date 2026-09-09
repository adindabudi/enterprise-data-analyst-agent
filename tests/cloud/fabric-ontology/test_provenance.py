import pytest
from eda_worker.acceptance.fabric_ontology import OntologyCloudObservations

pytestmark = pytest.mark.cloud


def test_ontology_provenance_reconciles(ontology_observations: OntologyCloudObservations) -> None:
    assert ontology_observations.provenance_reconciled is True
    assert ontology_observations.controls["provenance"] == "passed"
