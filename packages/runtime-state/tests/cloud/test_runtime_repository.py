from __future__ import annotations

import hashlib
import os
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from azure.cosmos.aio import CosmosClient
from azure.cosmos.exceptions import CosmosResourceNotFoundError
from azure.identity.aio import AzureCliCredential
from eda_contracts.controls import CommandKind
from eda_contracts.tasks import TaskStatus
from eda_runtime_state.models import OperationKeyInput, OperationStatus, TaskRecord, canonical_operation_key
from eda_runtime_state.tasks import CosmosRuntimeStateRepository

pytestmark = [pytest.mark.cloud, pytest.mark.anyio]


def cloud_setting(name: str) -> str:
    value = os.getenv(name)
    if value is None:
        pytest.skip(f"{name} is required for cloud tests")
    return value


def command_id(task_id: str, idempotency_key: str) -> str:
    digest = hashlib.sha256("\0".join((task_id, idempotency_key)).encode()).hexdigest()
    return f"cmd_{digest[:32]}"


async def test_runtime_repository_uses_opaque_locator_and_owner_hpk() -> None:
    endpoint = cloud_setting("EDA_COSMOS_ENDPOINT")
    database_name = cloud_setting("EDA_COSMOS_DATABASE")
    workspace_name = cloud_setting("EDA_COSMOS_WORKSPACE_CONTAINER")
    runtime_name = cloud_setting("EDA_COSMOS_RUNTIME_CONTAINER")
    credential = AzureCliCredential()
    client = CosmosClient(endpoint, credential=credential)
    database = client.get_database_client(database_name)
    workspace = database.get_container_client(workspace_name)
    runtime = database.get_container_client(runtime_name)
    repository = CosmosRuntimeStateRepository(workspace, runtime)
    now = datetime.now(UTC)
    task_id = f"task_{secrets.token_urlsafe(12)}"
    task = TaskRecord(
        id=task_id,
        tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
        owner_object_id=UUID("22222222-2222-2222-2222-222222222222"),
        session_id="ses_1234567890abcdef",
        status=TaskStatus.PLANNING,
        checkpoint_sequence=0,
        command_sequence=0,
        applied_command_sequence=0,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(minutes=15),
    )

    try:
        created = await repository.create_task(task, "request-12345678")
        # Compare against the record the store actually wrote: Cosmos assigns the
        # etag, so the pre-insert object can never be equal to what is read back.
        assert await repository.resolve_task(task.id) == created
        first = await repository.append_command(
            task.partition(), task.id, CommandKind.STEER, "regional check", "command-1"
        )
        second = await repository.append_command(task.partition(), task.id, CommandKind.CANCEL, None, "command-2")
        assert [item.sequence for item in await repository.pending_commands(task.id)] == [1, 2]
        assert second.sequence == first.sequence + 1
        operation = await repository.begin_operation(task, "sandbox-execute", {"script": "script-1"})
        await repository.complete_operation(task, operation.id, "result-7")
        replay = await repository.begin_operation(task, "sandbox-execute", {"script": "script-1"})
        assert replay.status is OperationStatus.COMPLETED
        assert replay.immutable_result_ref == "result-7"
        locator = await runtime.read_item(item=task.id, partition_key=task.id)
        assert "regional check" not in str(locator)
    finally:
        command_ids = [
            command_id(task.id, "command-1"),
            command_id(task.id, "command-2"),
        ]
        operation_id = canonical_operation_key(
            OperationKeyInput(
                workflow_instance_id=task.id, step_name="sandbox-execute", canonical_input={"script": "script-1"}
            )
        )
        for item_id in [task.id, *command_ids, operation_id]:
            try:
                await workspace.delete_item(item=item_id, partition_key=task.partition().values())
            except CosmosResourceNotFoundError:
                pass
        try:
            await runtime.delete_item(item=task.id, partition_key=task.id)
        except CosmosResourceNotFoundError:
            pass
        await client.close()
        await credential.close()
