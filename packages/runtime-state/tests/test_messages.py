from __future__ import annotations

from uuid import UUID

import pytest
from azure.cosmos.exceptions import CosmosHttpResponseError
from eda_runtime_state.messages import CosmosMessageRepository, InMemoryMessageRepository, MessageConflict
from eda_runtime_state.models import TaskPartition


def partition(owner: str = "22222222-2222-2222-2222-222222222222") -> TaskPartition:
    return TaskPartition(
        tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
        owner_object_id=UUID(owner),
        session_id="ses_1234567890abcdef",
    )


class FakeCosmosContainer:
    def __init__(self) -> None:
        self._items: dict[tuple[str, str, str, str], dict[str, object]] = {}
        self.read_calls: list[tuple[str, tuple[str, str, str]]] = []

    async def create_item(self, body: dict[str, object]) -> dict[str, object]:
        key = self._key_from_body(body)
        if key in self._items:
            raise CosmosHttpResponseError(status_code=409, message="conflict")
        self._items[key] = dict(body)
        return dict(body)

    async def read_item(self, item: str, partition_key: list[str]) -> dict[str, object]:
        key = (partition_key[0], partition_key[1], partition_key[2], item)
        self.read_calls.append((item, (partition_key[0], partition_key[1], partition_key[2])))
        stored = self._items.get(key)
        if stored is None:
            raise CosmosHttpResponseError(status_code=404, message="not found")
        return dict(stored)

    @staticmethod
    def _key_from_body(body: dict[str, object]) -> tuple[str, str, str, str]:
        return (
            str(body["tenantId"]),
            str(body["ownerObjectId"]),
            str(body["sessionId"]),
            str(body["id"]),
        )


@pytest.mark.asyncio
async def test_in_memory_append_user_is_deterministic_and_idempotent() -> None:
    repo = InMemoryMessageRepository()
    value = partition()

    first = await repo.append_user(value, "Analyze revenue", "request-1")
    second = await repo.append_user(value, "Analyze revenue", "request-1")

    assert first.id == second.id
    assert first == second


@pytest.mark.asyncio
async def test_in_memory_append_user_conflict_on_changed_text() -> None:
    repo = InMemoryMessageRepository()
    value = partition()
    await repo.append_user(value, "Analyze revenue", "request-1")

    with pytest.raises(MessageConflict):
        await repo.append_user(value, "Analyze margin", "request-1")


@pytest.mark.asyncio
async def test_in_memory_get_owned_isolated_by_full_partition() -> None:
    repo = InMemoryMessageRepository()
    owner = partition()
    other = partition("33333333-3333-3333-3333-333333333333")
    created = await repo.append_user(owner, "Analyze revenue", "request-1")

    assert await repo.get_owned(owner, created.id) is not None
    assert await repo.get_owned(other, created.id) is None


@pytest.mark.asyncio
async def test_cosmos_append_user_idempotent_retry_uses_point_read() -> None:
    value = partition()
    container = FakeCosmosContainer()
    repo = CosmosMessageRepository(container=container)  # type: ignore[arg-type]

    created = await repo.append_user(value, "Analyze revenue", "request-1")
    replayed = await repo.append_user(value, "Analyze revenue", "request-1")

    assert created.id == replayed.id
    assert container.read_calls[-1] == (created.id, tuple(value.values()))


@pytest.mark.asyncio
async def test_cosmos_append_user_conflict_when_retry_text_changes() -> None:
    value = partition()
    container = FakeCosmosContainer()
    repo = CosmosMessageRepository(container=container)  # type: ignore[arg-type]
    await repo.append_user(value, "Analyze revenue", "request-1")

    with pytest.raises(MessageConflict):
        await repo.append_user(value, "Analyze margin", "request-1")


@pytest.mark.asyncio
async def test_cosmos_get_owned_returns_none_for_missing_item() -> None:
    value = partition()
    container = FakeCosmosContainer()
    repo = CosmosMessageRepository(container=container)  # type: ignore[arg-type]

    assert await repo.get_owned(value, "msg_12345678") is None
