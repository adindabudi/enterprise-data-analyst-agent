from __future__ import annotations

import pytest
from eda_worker.acceptance.fabric_ontology import AcceptanceEvidence, validate_ontology_acceptance


def evidence(**overrides: object) -> AcceptanceEvidence:
    values: dict[str, object] = {
        "provider": "ontology",
        "state": "configured",
        "topology": "cross_tenant",
        "provider_contract_digest": "a" * 64,
        "auth_contract_digest": "b" * 64,
        "run_id": "run_01HZZZZZZZZZZZZZZZZZZZZZZZ",
    }
    values.update(overrides)
    return AcceptanceEvidence.model_validate(values)


def test_acceptance_requires_cross_tenant_configured_ontology_evidence() -> None:
    accepted = validate_ontology_acceptance(evidence())

    assert accepted.provider == "ontology"
    assert accepted.topology == "cross_tenant"


@pytest.mark.parametrize(
    "overrides",
    [
        {"topology": "same_tenant"},
        {"state": "ready"},
        {"provider": "semantic_model"},
    ],
)
def test_acceptance_rejects_nonpromoting_or_wrong_provider_evidence(overrides: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        validate_ontology_acceptance(evidence(**overrides))
