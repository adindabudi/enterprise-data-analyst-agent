from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest
from azure.cosmos.aio import CosmosClient
from azure.identity.aio import AzureCliCredential
from eda_worker.fabric.mcp_client import FabricMcpClient, json_object, object_list
from eda_worker.fabric.readiness import FabricProviderContract, validate_runtime_contract

pytestmark = [pytest.mark.cloud, pytest.mark.anyio]
ROOT = Path(__file__).resolve().parents[3]
PINNED_TOOLS = ROOT / "services" / "worker" / "tests" / "fabric" / "fixtures" / "tools-list-six.json"
POWER_BI_SCOPE = "https://analysis.windows.net/powerbi/api/.default"


def cloud_setting(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if not value:
        pytest.skip(f"{name} is required for cloud tests")
    return value


def pinned_tools() -> list[dict[str, object]]:
    payload: object = json.loads(PINNED_TOOLS.read_text(encoding="utf-8"))
    document = json_object(payload)
    return [json_object(item) for item in object_list(document["tools"])]


async def test_live_six_tool_contract_matches_published_runtime_record() -> None:
    cosmos_endpoint = cloud_setting("EDA_COSMOS_ENDPOINT")
    database = cloud_setting("EDA_COSMOS_DATABASE", "enterprise-data-analyst")
    runtime_container = cloud_setting("EDA_COSMOS_RUNTIME_CONTAINER", "runtime")
    fabric_tenant_id = UUID(cloud_setting("FABRIC_TENANT_ID"))
    model_deployment = cloud_setting("EDA_FOUNDRY_MODEL_DEPLOYMENT")
    base_model = cloud_setting("EDA_FOUNDRY_BASE_MODEL")
    deployment_id = cloud_setting("EDA_DEPLOYMENT_ID")
    product_credential = AzureCliCredential(tenant_id=cloud_setting("EDA_ENTRA_TENANT_ID"))
    fabric_credential = AzureCliCredential(tenant_id=str(fabric_tenant_id))
    cosmos = CosmosClient(cosmos_endpoint, credential=product_credential)
    container = cosmos.get_database_client(database).get_container_client(runtime_container)
    try:
        feature = cast(
            dict[str, Any],
            await container.read_item(item="feature:fabric", partition_key="feature:fabric"),
        )
        assert feature.get("state") in {"configured", "ready"}
        digest = feature.get("providerContractSha256")
        assert isinstance(digest, str)
        contract_id = f"feature-contract:fabric:{digest}"
        immutable = cast(dict[str, Any], await container.read_item(item=contract_id, partition_key=contract_id))
        contract = FabricProviderContract.model_validate(immutable.get("contract"))
        validate_runtime_contract(
            contract,
            expected_tools=pinned_tools(),
            fabric_tenant_id=fabric_tenant_id,
            model_deployment=model_deployment,
            base_model=base_model,
            deployment_id=deployment_id,
            now=contract.probed_at,
        )
        token = await fabric_credential.get_token(POWER_BI_SCOPE)

        async def token_provider() -> str:
            return token.token

        client = FabricMcpClient(token_provider=token_provider, expected_tools=pinned_tools())
        live_tools = await client.discover_tools()
        published_tools = {
            tool.name: {
                "name": tool.name,
                "description": tool.description,
                "inputSchema": tool.input_schema,
            }
            for tool in contract.tools
        }
        assert live_tools == published_tools
    finally:
        await cosmos.close()
        await fabric_credential.close()
        await product_credential.close()
