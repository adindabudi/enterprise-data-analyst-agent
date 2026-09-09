from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

import pytest
from azure.cosmos import exceptions as cosmos_exceptions
from eda_worker.history.models import CanonicalMessage, ProjectionDocument, SessionPartition, TodoProjection
from eda_worker.history.repository import (
    CosmosProjectionRepository,
    InMemoryProjectionRepository,
    MessageConflict,
    ProjectionConflict,
)

BatchOperation = tuple[str, tuple[object, ...]] | tuple[str, tuple[object, ...], dict[str, object]]


def test_projection_documents_serialize_workspace_partition_fields() -> None:
    now = datetime.now(UTC)
    projection = ProjectionDocument(
        tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
        owner_object_id=UUID("22222222-2222-2222-2222-222222222222"),
        session_id="ses_1234567890abcdef",
        projection_version=1,
        summary_version=0,
        messages=(),
        agent_session={"service_session_id": None},
        projected_tokens=0,
        updated_at=now,
    )

    document = projection.model_dump(mode="json", by_alias=True)

    assert {"tenantId", "ownerObjectId", "sessionId", "recordType"} <= set(document)
    assert "tenant_id" not in document


def partition(owner: str = "22222222-2222-2222-2222-222222222222") -> SessionPartition:
    return SessionPartition(
        tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
        owner_object_id=UUID(owner),
        session_id="ses_1234567890abcdef",
    )


def canonical_user_message(value: SessionPartition, *, message_id: str, text: str) -> CanonicalMessage:
    return CanonicalMessage(
        id=message_id,
        tenant_id=str(value.tenant_id),
        owner_object_id=str(value.owner_object_id),
        session_id=value.session_id,
        role="user",
        text=text,
        created_at=datetime.now(UTC),
    )


def empty_projection(value: SessionPartition) -> ProjectionDocument:
    return ProjectionDocument(
        tenant_id=value.tenant_id,
        owner_object_id=value.owner_object_id,
        session_id=value.session_id,
        projection_version=1,
        summary_version=0,
        messages=(),
        agent_session={"service_session_id": None},
        projected_tokens=0,
        updated_at=datetime.now(UTC),
    )


def empty_todos(value: SessionPartition) -> TodoProjection:
    return TodoProjection(
        tenant_id=value.tenant_id,
        owner_object_id=value.owner_object_id,
        session_id=value.session_id,
        items=(),
        updated_at=datetime.now(UTC),
    )


@pytest.fixture
def repository() -> InMemoryProjectionRepository:
    return InMemoryProjectionRepository()


@pytest.mark.asyncio
async def test_canonical_message_is_append_only(repository: InMemoryProjectionRepository) -> None:
    value = partition()
    message = canonical_user_message(value, message_id="msg_12345678", text="Analyze revenue")
    await repository.append_canonical(message)

    with pytest.raises(MessageConflict):
        await repository.append_canonical(message.model_copy(update={"text": "changed"}))


@pytest.mark.asyncio
async def test_projection_is_private_to_full_partition(repository: InMemoryProjectionRepository) -> None:
    owner_partition = partition()
    other_partition = partition("44444444-4444-4444-4444-444444444444")
    await repository.save_projection(
        owner_partition, empty_projection(owner_partition), empty_todos(owner_partition), None
    )

    assert await repository.load_projection(owner_partition) is not None
    assert await repository.load_projection(other_partition) is None


@pytest.mark.asyncio
async def test_in_memory_load_canonical_preserves_requested_order(repository: InMemoryProjectionRepository) -> None:
    value = partition()
    first = canonical_user_message(value, message_id="msg_12345678", text="first")
    second = canonical_user_message(value, message_id="msg_abcdefgh", text="second")
    await repository.append_canonical(first)
    await repository.append_canonical(second)

    loaded = await repository.load_canonical(value, [second.id, "msg_missing12", first.id])

    assert [item.id if item is not None else None for item in loaded] == [second.id, None, first.id]


def test_projection_forbids_service_conversation_and_reasoning() -> None:
    body = empty_projection(partition()).model_dump(mode="json", by_alias=True)

    assert body["agentSession"]["service_session_id"] is None
    assert "text_reasoning" not in str(body)
    assert "protected_data" not in str(body)


class FakeCosmosContainer:
    def __init__(self) -> None:
        self._items: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        self._etag_counter = 0
        self.read_calls: list[tuple[str, tuple[str, str, str]]] = []
        self.batch_calls: list[tuple[list[BatchOperation], tuple[str, str, str]]] = []
        self.next_batch_error: Exception | None = None

    async def create_item(self, body: dict[str, Any]) -> dict[str, Any]:
        key = self._key_from_body(body)
        if key in self._items:
            raise cosmos_exceptions.CosmosHttpResponseError(status_code=409, message="conflict")
        saved = self._with_etag(body)
        self._items[key] = saved
        return deepcopy(saved)

    async def read_item(self, item: str, partition_key: list[str]) -> dict[str, Any]:
        key = (partition_key[0], partition_key[1], partition_key[2], item)
        self.read_calls.append((item, (partition_key[0], partition_key[1], partition_key[2])))
        body = self._items.get(key)
        if body is None:
            raise cosmos_exceptions.CosmosHttpResponseError(status_code=404, message="not found")
        return deepcopy(body)

    async def execute_item_batch(
        self,
        batch_operations: Sequence[BatchOperation],
        partition_key: list[str],
    ) -> list[dict[str, Any]]:
        partition = (partition_key[0], partition_key[1], partition_key[2])
        self.batch_calls.append((list(batch_operations), partition))
        if self.next_batch_error is not None:
            error = self.next_batch_error
            self.next_batch_error = None
            raise error
        for operation in batch_operations:
            kind = operation[0]
            args = operation[1]
            options: dict[str, object]
            options = operation[2] if len(operation) == 3 else {}
            if kind == "create":
                body = args[0]
                if not isinstance(body, dict):
                    raise TypeError("create operation requires an item body")
                item = cast(dict[str, Any], body)
                key = (partition[0], partition[1], partition[2], str(item["id"]))
                if key in self._items:
                    raise cosmos_exceptions.CosmosBatchOperationError(headers={}, status_code=409, message="conflict")
                self._items[key] = self._with_etag(item)
                continue
            if kind == "replace":
                item_id = str(args[0])
                body = args[1]
                if not isinstance(body, dict):
                    raise TypeError("replace operation requires an item body")
                item = cast(dict[str, Any], body)
                key = (partition[0], partition[1], partition[2], item_id)
                current = self._items.get(key)
                if current is None:
                    raise cosmos_exceptions.CosmosBatchOperationError(headers={}, status_code=404, message="not found")
                if_match = options.get("if_match_etag")
                if if_match is not None and if_match != current.get("_etag"):
                    raise cosmos_exceptions.CosmosBatchOperationError(
                        headers={}, status_code=412, message="precondition failed"
                    )
                self._items[key] = self._with_etag(item)
                continue
            raise ValueError(f"unsupported operation {kind}")
        return []

    def seed_item(self, partition: SessionPartition, body: dict[str, Any]) -> None:
        partition_values = partition.values()
        key = (partition_values[0], partition_values[1], partition_values[2], str(body["id"]))
        self._items[key] = self._with_etag(body)

    def _with_etag(self, body: dict[str, Any]) -> dict[str, Any]:
        self._etag_counter += 1
        saved = deepcopy(body)
        saved["_etag"] = f'"{self._etag_counter}"'
        return saved

    @staticmethod
    def _key_from_body(body: dict[str, Any]) -> tuple[str, str, str, str]:
        return (
            str(body["tenantId"]),
            str(body["ownerObjectId"]),
            str(body["sessionId"]),
            str(body["id"]),
        )


@pytest.mark.asyncio
async def test_cosmos_load_projection_uses_point_read_with_full_partition() -> None:
    value = partition()
    container = FakeCosmosContainer()
    repository = CosmosProjectionRepository(container=container)  # type: ignore[arg-type]
    stored_projection = empty_projection(value).model_dump(mode="json", by_alias=True)
    container.seed_item(value, stored_projection)

    loaded = await repository.load_projection(value)

    assert loaded is not None
    assert container.read_calls[-1] == ("projection", tuple(value.values()))


@pytest.mark.asyncio
async def test_cosmos_load_canonical_preserves_order_and_uses_full_partition() -> None:
    value = partition()
    container = FakeCosmosContainer()
    repository = CosmosProjectionRepository(container=container)  # type: ignore[arg-type]
    first = canonical_user_message(value, message_id="msg_12345678", text="first")
    second = canonical_user_message(value, message_id="msg_abcdefgh", text="second")
    container.seed_item(value, first.model_dump(mode="json", by_alias=True))
    container.seed_item(value, second.model_dump(mode="json", by_alias=True))

    loaded = await repository.load_canonical(value, [second.id, "msg_missing12", first.id])

    assert [item.id if item is not None else None for item in loaded] == [second.id, None, first.id]
    assert container.read_calls[-3:] == [
        (second.id, tuple(value.values())),
        ("msg_missing12", tuple(value.values())),
        (first.id, tuple(value.values())),
    ]


@pytest.mark.asyncio
async def test_cosmos_save_projection_creates_projection_and_todo_in_one_batch() -> None:
    value = partition()
    container = FakeCosmosContainer()
    repository = CosmosProjectionRepository(container=container)  # type: ignore[arg-type]

    saved = await repository.save_projection(value, empty_projection(value), empty_todos(value), None)

    assert saved.id == "projection"
    operations, partition_key = container.batch_calls[-1]
    assert partition_key == tuple(value.values())
    assert [operation[0] for operation in operations] == ["create", "create"]
    assert operations[0][1][0]["id"] == "projection"  # type: ignore[index]
    assert operations[1][1][0]["id"] == "todo-projection"  # type: ignore[index]


@pytest.mark.asyncio
async def test_cosmos_save_projection_replaces_projection_with_expected_etag() -> None:
    value = partition()
    container = FakeCosmosContainer()
    repository = CosmosProjectionRepository(container=container)  # type: ignore[arg-type]

    first = await repository.save_projection(value, empty_projection(value), empty_todos(value), None)
    next_projection = empty_projection(value).model_copy(update={"projection_version": 2})
    await repository.save_projection(value, next_projection, empty_todos(value), first.etag)

    operations, _ = container.batch_calls[-1]
    assert [operation[0] for operation in operations] == ["replace", "replace"]
    assert len(operations[0]) == 3
    assert operations[0][2] == {"if_match_etag": first.etag}  # type: ignore[index]
    assert len(operations[1]) == 2


@pytest.mark.asyncio
async def test_cosmos_save_projection_maps_412_to_projection_conflict() -> None:
    value = partition()
    container = FakeCosmosContainer()
    repository = CosmosProjectionRepository(container=container)  # type: ignore[arg-type]

    container.next_batch_error = cosmos_exceptions.CosmosBatchOperationError(
        headers={},
        status_code=412,
        message="precondition failed",
    )

    with pytest.raises(ProjectionConflict):
        await repository.save_projection(
            value,
            empty_projection(value).model_copy(update={"projection_version": 2}),
            empty_todos(value),
            '"stale"',
        )


@pytest.mark.asyncio
async def test_cosmos_append_canonical_is_idempotent_for_identical_conflict() -> None:
    value = partition()
    container = FakeCosmosContainer()
    repository = CosmosProjectionRepository(container=container)  # type: ignore[arg-type]
    message = canonical_user_message(value, message_id="msg_abcdef12", text="Analyze revenue")

    await repository.append_canonical(message)
    await repository.append_canonical(message)

    assert container.read_calls[-1] == (message.id, tuple(value.values()))


@pytest.mark.asyncio
async def test_cosmos_append_canonical_conflict_raises_message_conflict() -> None:
    value = partition()
    container = FakeCosmosContainer()
    repository = CosmosProjectionRepository(container=container)  # type: ignore[arg-type]
    message = canonical_user_message(value, message_id="msg_abcdef12", text="Analyze revenue")
    await repository.append_canonical(message)

    with pytest.raises(MessageConflict):
        await repository.append_canonical(message.model_copy(update={"text": "changed"}))
