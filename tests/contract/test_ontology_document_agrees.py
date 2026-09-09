from __future__ import annotations

import json

from eda_api.fabric_ontology import OntologyTarget as ApiOntologyTarget
from eda_worker.fabric.ontology.config import OntologyTarget as WorkerOntologyTarget

# One FABRIC_ONTOLOGIES_JSON document is read by both services, and both forbid extras.
DOCUMENT = {
    "workspaceId": "b82afbde-8304-44c0-ac94-3cf69f6da909",
    "ontologyId": "5566b159-6998-4bb8-a167-6d5bf3c43352",
    "graphModelId": "bdf1d01e-7da8-4b58-873a-54d8edb12f34",
    "description": "Lamna healthcare operations ontology",
    "routingTerms": ["hospitals", "patients"],
}


def test_both_services_accept_the_same_ontology_document() -> None:
    payload = json.dumps(DOCUMENT)

    api = ApiOntologyTarget.model_validate_json(payload)
    worker = WorkerOntologyTarget.model_validate_json(payload)

    assert api.workspace_id == worker.workspace_id
    assert api.ontology_id == worker.ontology_id
    assert api.graph_model_id == worker.graph_model_id


def test_the_two_models_declare_the_same_fields() -> None:
    assert set(ApiOntologyTarget.model_fields) == set(WorkerOntologyTarget.model_fields)
