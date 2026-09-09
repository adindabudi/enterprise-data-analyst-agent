from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from eda_api.auth.models import Principal
from eda_contracts.tasks import TaskStatus
from eda_runtime_state.messages import CanonicalMessage
from eda_runtime_state.models import TaskRecord


class StoredWorkspace:
    def __init__(self, records: list[dict[str, Any]]) -> None:
        self.records = records
        self.partitions: list[list[str]] = []

    def query_items(self, *, query: str, partition_key: list[str]):
        self.partitions.append(partition_key)
        assert "recordType" in query

        async def records():
            for record in self.records:
                if [record["tenantId"], record["ownerObjectId"], record["sessionId"]] == partition_key:
                    yield record

        return records()


@pytest.mark.asyncio
async def test_history_survives_reader_recreation_and_stays_owner_scoped() -> None:
    from eda_api.storage.history import CosmosSessionHistoryReader

    principal = Principal(tenant_id=UUID(int=1), owner_object_id=UUID(int=2), audience=UUID(int=3))
    session_id = "ses_persistent_12345678"
    now = datetime.now(UTC)
    message = CanonicalMessage(
        id="msg_persistent_12345678", tenant_id=str(principal.tenant_id),
        owner_object_id=str(principal.owner_object_id), session_id=session_id,
        role="user", text="Export occupancy to Excel", created_at=now,
    )
    first_task = TaskRecord(
        id="task_first_12345678", tenant_id=principal.tenant_id, owner_object_id=principal.owner_object_id,
        session_id=session_id, status=TaskStatus.COMPLETED, checkpoint_sequence=7,
        command_sequence=0, applied_command_sequence=0, source_message_id=message.id,
        created_at=now, updated_at=now, expires_at=now + timedelta(days=30),
    )
    second_task = first_task.model_copy(update={"id": "task_second_12345678", "created_at": now + timedelta(seconds=1)})
    workspace = StoredWorkspace([
        second_task.model_dump(mode="json"), message.model_dump(mode="json"), first_task.model_dump(mode="json"),
        {**message.model_dump(mode="json"), "recordType": "operation", "text": "private tool data"},
    ])

    history = await CosmosSessionHistoryReader(workspace).read_history(principal, session_id)
    restored = await CosmosSessionHistoryReader(workspace).read_history(principal, session_id)

    assert restored == history
    assert [entry.message_id for entry in history.messages] == [message.id]
    assert history.messages[0].text == message.text
    assert [entry.task_id for entry in history.tasks] == [first_task.id, second_task.id]
    assert history.tasks[0].source_message_id == message.id
    assert "private tool data" not in history.model_dump_json()
    assert "ownerObjectId" not in history.model_dump_json()
    assert workspace.partitions == [[str(principal.tenant_id), str(principal.owner_object_id), session_id]] * 2

    other_owner = principal.model_copy(update={"owner_object_id": UUID(int=4)})
    hidden = await CosmosSessionHistoryReader(workspace).read_history(other_owner, session_id)
    assert not hidden.messages and not hidden.tasks


@pytest.mark.asyncio
async def test_session_list_selects_only_session_records() -> None:
    from eda_api.storage.workspace import CosmosWorkspaceRepository, InMemoryWorkspaceRepository

    principal = Principal(tenant_id=UUID(int=1), owner_object_id=UUID(int=2), audience=UUID(int=3))
    session = await InMemoryWorkspaceRepository().create_session(principal, "Persisted analysis")

    class Workspace:
        def query_items(self, *, query, parameters):
            assert "c.recordType = 'session'" in query
            assert parameters == [
                {"name": "@tenantId", "value": str(principal.tenant_id)},
                {"name": "@ownerObjectId", "value": str(principal.owner_object_id)},
            ]

            async def records():
                yield session.model_dump(mode="json", by_alias=True)

            return records()

    assert await CosmosWorkspaceRepository(Workspace()).list_sessions(principal) == [session]