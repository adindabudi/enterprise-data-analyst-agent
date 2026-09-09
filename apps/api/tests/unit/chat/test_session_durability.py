from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from azure.cosmos.exceptions import CosmosResourceNotFoundError
from eda_runtime_state.models import TaskPartition


class StoredContainer:
    def __init__(self) -> None:
        self.documents: dict[tuple[str, ...], dict] = {}

    async def read_item(self, *, item: str, partition_key: list[str]):
        try:
            return self.documents[(*partition_key, item)]
        except KeyError:
            raise CosmosResourceNotFoundError(message="not found") from None

    async def upsert_item(self, *, body: dict):
        key = (body["tenantId"], body["ownerObjectId"], body["sessionId"], body["id"])
        self.documents[key] = json.loads(json.dumps(body))


@pytest.mark.asyncio
async def test_agent_session_and_queries_survive_cache_loss_and_reader_restart() -> None:
    from eda_api.chat.sessions import CosmosInteractiveSessionStore

    partition = TaskPartition(tenant_id=UUID(int=1), owner_object_id=UUID(int=2), session_id="ses_durable_12345678")
    container = StoredContainer()
    state = {"session": {"messages": ["previous answer"]}, "queryRuns": [{"rows": [{"rooms": 414}]}]}
    fallback = SimpleNamespace(load=AsyncMock(return_value=state))
    original = CosmosInteractiveSessionStore(container, fallback=fallback)

    assert await original.load(partition) == state
    restored = CosmosInteractiveSessionStore(container)
    assert await restored.load(partition) == state
    await restored.save(partition, {**state, "session": {"messages": ["new answer"]}})
    assert (await CosmosInteractiveSessionStore(container).load(partition))["session"] == {"messages": ["new answer"]}
    assert await restored.load(partition.model_copy(update={"owner_object_id": UUID(int=3)})) is None
    fallback.load.assert_awaited_once_with(partition)


@pytest.mark.asyncio
async def test_oversized_state_never_reuses_a_stale_cache(caplog: pytest.LogCaptureFixture) -> None:
    from eda_api.chat.sessions import MAX_SESSION_STATE_CHARS, CosmosInteractiveSessionStore

    partition = TaskPartition(tenant_id=UUID(int=1), owner_object_id=UUID(int=2), session_id="ses_durable_12345678")
    fallback = SimpleNamespace(load=AsyncMock(return_value={"session": "old"}))
    store = CosmosInteractiveSessionStore(StoredContainer(), fallback=fallback)
    await store.save(partition, {"session": "x" * MAX_SESSION_STATE_CHARS})

    assert await store.load(partition) is None
    fallback.load.assert_not_called()
    assert "interactive session state exceeds the persistence budget" in caplog.text


@pytest.mark.asyncio
async def test_unavailable_legacy_cache_does_not_block_durable_sessions() -> None:
    from eda_api.chat.sessions import CosmosInteractiveSessionStore

    partition = TaskPartition(tenant_id=UUID(int=1), owner_object_id=UUID(int=2), session_id="ses_durable_12345678")
    container = StoredContainer()
    fallback = SimpleNamespace(load=AsyncMock(side_effect=ConnectionError("cache unavailable")))
    store = CosmosInteractiveSessionStore(container, fallback=fallback)

    assert await store.load(partition) is None
    await store.save(partition, {"session": {"messages": ["new answer"]}})
    assert await CosmosInteractiveSessionStore(container).load(partition) == {"session": {"messages": ["new answer"]}}


@pytest.mark.asyncio
@pytest.mark.parametrize("oversized", [False, True])
async def test_migration_returns_the_persisted_state_without_a_second_read(oversized: bool) -> None:
    from eda_api.chat.sessions import MAX_SESSION_STATE_CHARS, CosmosInteractiveSessionStore

    partition = TaskPartition(tenant_id=UUID(int=1), owner_object_id=UUID(int=2), session_id="ses_durable_12345678")
    container = SimpleNamespace(
        read_item=AsyncMock(side_effect=[CosmosResourceNotFoundError(message="not found"), TimeoutError()]),
        upsert_item=AsyncMock(),
    )
    state = {"session": "x" * MAX_SESSION_STATE_CHARS if oversized else "saved"}
    store = CosmosInteractiveSessionStore(container, fallback=SimpleNamespace(load=AsyncMock(return_value=state)))

    assert await store.load(partition) == (None if oversized else state)
    assert container.read_item.await_count == 1
    assert container.upsert_item.call_args.kwargs["body"]["state"] == (None if oversized else state)