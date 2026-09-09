from __future__ import annotations

import os
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from azure.cosmos.aio import ContainerProxy, CosmosClient
from azure.cosmos.exceptions import CosmosHttpResponseError, CosmosResourceNotFoundError
from azure.identity.aio import AzureCliCredential
from eda_api.auth.models import Principal
from eda_api.storage.models import WorkspaceSession
from eda_api.storage.workspace import CosmosWorkspaceRepository, StorageConflict

pytestmark = [pytest.mark.cloud, pytest.mark.anyio]


def cloud_setting(name: str) -> str:
    value = os.getenv(name)
    if value is None:
        pytest.skip(f"{name} is required for cloud tests")
    return value


def session_document(principal: Principal, session_id: str) -> dict[str, object]:
    now = datetime.now(UTC)
    session = WorkspaceSession(
        id=session_id,
        tenant_id=principal.tenant_id,
        owner_object_id=principal.owner_object_id,
        session_id=session_id,
        title="Cloud HPK contract",
        created_at=now,
        last_activity_at=now,
        expires_at=now + timedelta(minutes=5),
    )
    return session.model_dump(mode="json", by_alias=True, exclude={"etag"})


async def delete_if_present(container: ContainerProxy, principal: Principal, session_id: str) -> None:
    try:
        await container.delete_item(
            item=session_id,
            partition_key=[str(principal.tenant_id), str(principal.owner_object_id), session_id],
        )
    except CosmosResourceNotFoundError:
        pass


async def test_workspace_container_enforces_hpk_owner_and_etag_contract() -> None:
    endpoint = cloud_setting("EDA_COSMOS_ENDPOINT")
    database_name = cloud_setting("EDA_COSMOS_DATABASE")
    container_name = cloud_setting("EDA_COSMOS_WORKSPACE_CONTAINER")
    credential = AzureCliCredential()
    client = CosmosClient(endpoint, credential=credential)
    container = client.get_database_client(database_name).get_container_client(container_name)
    repository = CosmosWorkspaceRepository(container)
    owner = Principal(
        tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
        owner_object_id=UUID("22222222-2222-2222-2222-222222222222"),
        audience=UUID("33333333-3333-3333-3333-333333333333"),
    )
    other_owner = owner.model_copy(update={"owner_object_id": UUID("44444444-4444-4444-4444-444444444444")})
    batch_first_id = f"ses_{secrets.token_urlsafe(16)}"
    batch_second_id = f"ses_{secrets.token_urlsafe(16)}"
    created_session_id: str | None = None

    try:
        definition = await container.read()
        partition_key = definition["partitionKey"]
        assert partition_key["paths"] == ["/tenantId", "/ownerObjectId", "/sessionId"]
        assert partition_key["kind"] == "MultiHash"
        assert partition_key["version"] == 2

        created = await repository.create_session(owner, "Cloud HPK contract")
        created_session_id = created.session_id
        assert await repository.get_session(owner, created.session_id) is not None
        assert await repository.get_session(other_owner, created.session_id) is None

        await repository.rename_session(owner, created.session_id, "Updated", created.etag)
        with pytest.raises(StorageConflict):
            await repository.rename_session(owner, created.session_id, "Stale", created.etag)

        batch_operations = [
            ("create", (session_document(owner, batch_first_id),)),
            ("create", (session_document(owner, batch_second_id),)),
        ]
        try:
            result = await container.execute_item_batch(
                batch_operations=batch_operations,
                partition_key=CosmosWorkspaceRepository.partition(owner, batch_first_id),
            )
        except CosmosHttpResponseError:
            pass
        else:
            statuses: list[int] = []
            for item in result:
                status_code = item.get("statusCode")
                assert isinstance(status_code, int)
                statuses.append(status_code)
            assert any(status >= 400 for status in statuses)

        assert await repository.get_session(owner, batch_first_id) is None
        assert await repository.get_session(owner, batch_second_id) is None
    finally:
        for session_id in (batch_first_id, batch_second_id):
            await delete_if_present(container, owner, session_id)
        if created_session_id is not None:
            await repository.delete_session_records(owner, created_session_id)
        await client.close()
        await credential.close()
