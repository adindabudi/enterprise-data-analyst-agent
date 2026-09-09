import pytest
from eda_worker.acceptance.fabric_ontology import OntologyCloudObservations

pytestmark = pytest.mark.cloud


def test_schema_and_cold_warm_order(ontology_observations: OntologyCloudObservations) -> None:
    assert ontology_observations.tool_names == ("list_ontology_entity_types", "search_ontology")
    assert ontology_observations.cold_call_order[-2:] == ("list_ontology_entity_types", "search_ontology")
    assert ontology_observations.warm_call_order[-1] == "search_ontology"
    assert ontology_observations.controls["schema"] == "passed"
    assert ontology_observations.controls["cold_warm_order"] == "passed"
    assert ontology_observations.controls["cache_isolation"] == "passed"
