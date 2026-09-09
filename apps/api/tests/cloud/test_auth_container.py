from __future__ import annotations

import asyncio
import os
import secrets
from datetime import UTC, datetime, timedelta

import pytest
from azure.cosmos.aio import CosmosClient
from azure.cosmos.exceptions import CosmosResourceNotFoundError
from azure.identity.aio import AzureCliCredential
from eda_api.auth.models import AuthFlowRecord
from eda_api.auth.repository import CosmosAuthRepository

pytestmark = [pytest.mark.cloud, pytest.mark.anyio]


def cloud_setting(name: str) -> str:
    value = os.getenv(name)
    if value is None:
        pytest.skip(f"{name} is required for cloud tests")
    return value


async def test_cosmos_flow_pop_has_exactly_one_winner() -> None:
    endpoint = cloud_setting("EDA_COSMOS_ENDPOINT")
    database_name = cloud_setting("EDA_COSMOS_DATABASE")
    container_name = cloud_setting("EDA_COSMOS_AUTH_CONTAINER")
    credential = AzureCliCredential()
    client = CosmosClient(endpoint, credential=credential)
    flow_id = f"flow_cloud_{secrets.token_urlsafe(12)}"
    container = client.get_database_client(database_name).get_container_client(container_name)
    repository = CosmosAuthRepository(container)

    try:
        flow = AuthFlowRecord(
            id=flow_id,
            flow={"code_verifier": "synthetic-verifier"},
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
        await repository.put_flow(flow)

        pops = await asyncio.gather(repository.pop_flow(flow_id), repository.pop_flow(flow_id))

        assert sum(pop is not None for pop in pops) == 1
        assert flow in pops
    finally:
        try:
            await container.delete_item(item=flow_id, partition_key=flow_id)
        except CosmosResourceNotFoundError:
            pass
        await client.close()
        await credential.close()
