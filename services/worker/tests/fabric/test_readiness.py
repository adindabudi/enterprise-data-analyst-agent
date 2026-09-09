from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest
from eda_worker.fabric.config import FABRIC_IQ_MCP_URL, FABRIC_IQ_VARIANT
from eda_worker.fabric.readiness import (
    FabricFeatureRecord,
    FabricModelIdentity,
    FabricProviderContract,
    FabricReadinessStatus,
    build_provider_contract,
    evaluate_fabric_readiness,
    load_fabric_runtime_state,
    provider_contract_sha256,
    validate_runtime_contract,
)

FIXTURES = Path(__file__).parent / "fixtures"


def tool_fixture() -> list[dict[str, object]]:
    payload = cast(
        dict[str, object],
        json.loads((FIXTURES / "tools-list-six.json").read_text(encoding="utf-8")),
    )
    return cast(list[dict[str, object]], payload["tools"])


def build_contract(probed_at: datetime | None = None) -> FabricProviderContract:
    return build_provider_contract(
        tools=tool_fixture(),
        expected_tools=tool_fixture(),
        fabric_tenant_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        model_deployment="gpt-5.6-terra",
        base_model="gpt-5.6-terra",
        deployment_id="deployment-20260724",
        run_id="run-20260724",
        probed_at=probed_at or datetime.now(UTC),
    )


def test_manifest_contains_hashes_and_complete_normalized_schemas_without_topology() -> None:
    contract = build_contract()
    encoded = contract.model_dump_json(by_alias=True)

    expected_names = sorted(cast(str, tool["name"]) for tool in tool_fixture())
    assert contract.tool_names == tuple(expected_names)
    assert set(contract.tool_schema_hashes) == set(contract.tool_names)
    assert contract.runtime_allowlist == ("ExecuteQuery", "GetSemanticModelSchema", "ValueSearch")
    assert FABRIC_IQ_MCP_URL not in encoded
    assert FABRIC_IQ_VARIANT not in encoded
    assert "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa" not in encoded


def test_runtime_rejects_stale_or_wrong_deployment_contract() -> None:
    stale = build_contract(datetime.now(UTC) - timedelta(days=2))

    with pytest.raises(ValueError, match="stale"):
        validate_runtime_contract(
            stale,
            expected_tools=tool_fixture(),
            fabric_tenant_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
            model_deployment="gpt-5.6-terra",
            base_model="gpt-5.6-terra",
            deployment_id="deployment-20260724",
            now=datetime.now(UTC),
        )
    with pytest.raises(ValueError, match="deployment"):
        validate_runtime_contract(
            build_contract(),
            expected_tools=tool_fixture(),
            fabric_tenant_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
            model_deployment="gpt-5.6-terra",
            base_model="gpt-5.6-terra",
            deployment_id="another-deployment",
            now=datetime.now(UTC),
        )


def model_identity(**updates: str | None) -> FabricModelIdentity:
    values: dict[str, object] = {
        "modelProfile": "gpt-5.6-terra-medium-v1",
        "modelDeployment": "gpt-5.6-terra",
        "servedModel": "gpt-5.6-terra",
        "servedSnapshot": "2026-07-09",
        "promptVersion": "gpt-5.6-terra-v1",
        "promptSha256": "1" * 64,
        "requestOptionsSha256": "2" * 64,
    }
    values.update(updates)
    return FabricModelIdentity.model_validate(values)


def feature_record(contract: FabricProviderContract, *, state: str = "configured") -> FabricFeatureRecord:
    identity = model_identity()
    return FabricFeatureRecord.model_validate(
        {
            "id": "feature:fabric",
            "provider": "semantic_model",
            "state": state,
            "deploymentId": contract.deployment_id,
            "providerContractSha256": provider_contract_sha256(contract),
            **identity.model_dump(mode="json", by_alias=True),
            "acceptanceEvidenceSha256": "3" * 64 if state == "ready" else None,
            "verifiedAt": datetime.now(UTC).isoformat(),
        }
    )


def evaluate(
    *,
    enabled: bool,
    feature: FabricFeatureRecord | None,
    contract: FabricProviderContract | None,
    identity: FabricModelIdentity | None = None,
) -> FabricReadinessStatus:
    return evaluate_fabric_readiness(
        enabled=enabled,
        feature=feature,
        contract=contract,
        expected_tools=tool_fixture(),
        fabric_tenant_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        active_model=identity or model_identity(),
        deployment_id="deployment-20260724",
        now=datetime.now(UTC),
    ).status


def test_fail_closed_fabric_readiness_state_table() -> None:
    contract = build_contract()

    assert evaluate(enabled=False, feature=None, contract=None) is FabricReadinessStatus.DISABLED
    assert evaluate(enabled=True, feature=None, contract=None) is FabricReadinessStatus.FAILED
    assert (
        evaluate(enabled=True, feature=feature_record(contract), contract=contract) is FabricReadinessStatus.CONFIGURED
    )
    assert (
        evaluate(enabled=True, feature=feature_record(contract, state="ready"), contract=contract)
        is FabricReadinessStatus.READY
    )


def test_ready_evidence_for_another_prompt_or_options_stays_configured() -> None:
    contract = build_contract()
    feature = feature_record(contract, state="ready")

    assert (
        evaluate(
            enabled=True,
            feature=feature,
            contract=contract,
            identity=model_identity(promptSha256="4" * 64),
        )
        is FabricReadinessStatus.CONFIGURED
    )
    assert (
        evaluate(
            enabled=True,
            feature=feature,
            contract=contract,
            identity=model_identity(requestOptionsSha256="5" * 64),
        )
        is FabricReadinessStatus.CONFIGURED
    )


class FakeRuntimeContainer:
    def __init__(self, documents: dict[str, dict[str, object]]) -> None:
        self.documents = documents
        self.reads: list[str] = []

    async def read_item(self, item: str, partition_key: str) -> dict[str, object]:
        assert item == partition_key
        self.reads.append(item)
        document = self.documents.get(item)
        if document is None:
            raise ValueError("missing runtime document")
        return document


@pytest.mark.asyncio
async def test_runtime_loader_requires_complete_immutable_contract_record() -> None:
    contract = build_contract()
    feature = feature_record(contract, state="ready")
    digest = provider_contract_sha256(contract)
    contract_id = f"feature-contract:fabric:{digest}"
    container = FakeRuntimeContainer(
        {
            "feature:fabric": feature.model_dump(mode="json", by_alias=True),
            contract_id: {
                "id": contract_id,
                "recordType": "featureContract",
                "provider": "semantic_model",
                "immutable": True,
                "providerContractSha256": digest,
                "contract": contract.model_dump(mode="json", by_alias=True),
            },
        }
    )

    loaded = await load_fabric_runtime_state(
        container,
        enabled=True,
        fabric_tenant_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        active_model=model_identity(),
        deployment_id="deployment-20260724",
        now=datetime.now(UTC),
    )

    assert loaded.readiness.status is FabricReadinessStatus.READY
    assert loaded.contract == contract
    assert container.reads == ["feature:fabric", contract_id]

    tampered = FakeRuntimeContainer(
        {
            "feature:fabric": feature.model_dump(mode="json", by_alias=True),
            contract_id: {
                "id": contract_id,
                "recordType": "featureContract",
                "provider": "semantic_model",
                "immutable": True,
                "providerContractSha256": "f" * 64,
                "contract": contract.model_dump(mode="json", by_alias=True),
            },
        }
    )
    failed = await load_fabric_runtime_state(
        tampered,
        enabled=True,
        fabric_tenant_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        active_model=model_identity(),
        deployment_id="deployment-20260724",
        now=datetime.now(UTC),
    )
    assert failed.readiness.status is FabricReadinessStatus.FAILED
    assert failed.contract is None


@pytest.mark.asyncio
async def test_disabled_runtime_loader_performs_no_cosmos_reads() -> None:
    container = FakeRuntimeContainer({})

    loaded = await load_fabric_runtime_state(
        container,
        enabled=False,
        fabric_tenant_id=None,
        active_model=model_identity(),
        deployment_id="deployment-20260724",
        now=datetime.now(UTC),
    )

    assert loaded.readiness.status is FabricReadinessStatus.DISABLED
    assert loaded.contract is None
    assert container.reads == []
