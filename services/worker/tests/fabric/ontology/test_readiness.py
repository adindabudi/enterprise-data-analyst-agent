from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID

import pytest
from eda_worker.fabric.ontology.config import OntologyTarget
from eda_worker.fabric.ontology.mcp_client import EXPECTED_ONTOLOGY_TOOLS, EXPECTED_TOOL_DESCRIPTIONS
from eda_worker.fabric.ontology.readiness import (
    OntologyProviderRuntimeContract,
    build_tool_manifest,
    load_ontology_runtime_state,
    ontology_provider_contract_sha256,
)
from eda_worker.fabric.readiness import FabricModelIdentity, FabricReadinessStatus


def tool(name: str, description: str | None = None) -> dict[str, object]:
    return {
        "name": name,
        "description": EXPECTED_TOOL_DESCRIPTIONS[name] if description is None else description,
        "inputSchema": EXPECTED_ONTOLOGY_TOOLS[name],
    }


def test_builds_a_stable_manifest_for_the_accepted_two_tool_contract() -> None:
    manifest = build_tool_manifest([tool("search_ontology"), tool("list_ontology_entity_types")])

    assert manifest.tool_names == ("list_ontology_entity_types", "search_ontology")
    assert len(manifest.contract_digest) == 64


def test_rejects_description_or_tool_set_drift() -> None:
    with pytest.raises(ValueError, match="tool contract"):
        build_tool_manifest(
            [
                tool("list_ontology_entity_types", description="Changed preview description."),
                tool("search_ontology"),
            ]
        )


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


def runtime_contract() -> OntologyProviderRuntimeContract:
    grounding = {
        "entities": [
            {
                "name": "Patients",
                "keyProperties": ["PatientId"],
                "properties": [{"name": "PatientId", "valueType": "String"}],
                "timeSeriesProperties": [],
            }
        ]
    }
    guide = {
        "alias": "lamna-healthcare",
        "description": "Synthetic hospital operations.",
        "routingMode": "automatic",
        "routingTerms": ["PatientId", "Patients"],
    }
    manifest = build_tool_manifest([tool("list_ontology_entity_types"), tool("search_ontology")])
    return OntologyProviderRuntimeContract.model_validate(
        {
            "authContractSha256": "a" * 64,
            "deploymentSha256": "b" * 64,
            "endpointTemplateSha256": hashlib.sha256(
                b"https://api.fabric.microsoft.com/v1/mcp/dataPlane/workspaces/{workspace_id}/items/{ontology_id}/ontologyEndpoint"
            ).hexdigest(),
            "targetCatalogSha256": "c" * 64,
            "scopeSha256": "d" * 64,
            "audienceSha256": "e" * 64,
            "toolContractSha256": manifest.contract_digest,
            "toolNames": list(manifest.tool_names),
            "aliases": [
                {
                    "alias": "lamna-healthcare",
                    "targetPairSha256": "f" * 64,
                    "groundingSha256": digest(grounding),
                    "grounding": grounding,
                    "sourceGuideSha256": digest(guide),
                    "sourceGuide": guide,
                }
            ],
        }
    )


def active_model() -> FabricModelIdentity:
    return FabricModelIdentity(
        modelProfile="gpt-5.6-terra-medium-v1",
        modelDeployment="gpt-5.6-terra",
        servedModel="gpt-5.6-terra",
        servedSnapshot="2026-07-09",
        promptVersion="gpt-5.6-terra-v1",
        promptSha256="1" * 64,
        requestOptionsSha256="2" * 64,
    )


class RuntimeContainer:
    def __init__(self, documents: dict[str, dict[str, object]]) -> None:
        self.documents = documents

    async def read_item(self, item: str, partition_key: str) -> dict[str, object]:
        assert item == partition_key
        return self.documents[item]


@pytest.mark.asyncio
async def test_runtime_loader_uses_separate_contract_and_profile_bound_ready_state() -> None:
    contract = runtime_contract()
    contract_digest = ontology_provider_contract_sha256(contract)
    identity = active_model().model_dump(mode="json", by_alias=True)
    feature = {
        "id": "feature:fabric-ontology",
        "provider": "ontology",
        "state": "ready",
        "deploymentId": "deployment-20260724",
        "providerContractSha256": contract_digest,
        "acceptanceEvidenceSha256": "3" * 64,
        "verifiedAt": datetime.now(UTC).isoformat(),
        **identity,
    }
    contract_id = f"feature-contract:fabric-ontology:{contract_digest}"
    container = RuntimeContainer(
        {
            "feature:fabric-ontology": feature,
            contract_id: {
                "id": contract_id,
                "provider": "ontology",
                "immutable": True,
                "providerContractSha256": contract_digest,
                "contract": contract.model_dump(mode="json", by_alias=True),
            },
        }
    )
    catalog = {
        "lamna-healthcare": OntologyTarget(
            workspaceId=UUID("11111111-1111-1111-1111-111111111111"),
            ontologyId=UUID("22222222-2222-2222-2222-222222222222"),
            description="Synthetic hospital operations.",
        )
    }

    loaded = await load_ontology_runtime_state(
        container,
        enabled=True,
        catalog=catalog,
        active_model=active_model(),
        deployment_id="deployment-20260724",
    )
    mismatched = await load_ontology_runtime_state(
        container,
        enabled=True,
        catalog=catalog,
        active_model=active_model().model_copy(update={"prompt_sha256": "4" * 64}),
        deployment_id="deployment-20260724",
    )

    assert loaded.readiness.status is FabricReadinessStatus.READY
    assert loaded.contract == contract
    assert mismatched.readiness.status is FabricReadinessStatus.CONFIGURED
