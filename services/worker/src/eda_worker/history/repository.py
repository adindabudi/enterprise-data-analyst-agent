from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from azure.cosmos import exceptions as cosmos_exceptions
from azure.cosmos.aio import ContainerProxy

from .models import CanonicalMessage, ProjectionDocument, SessionPartition, TodoProjection


class MessageConflict(ValueError):
    pass


class ProjectionConflict(ValueError):
    pass


class ProjectionRepository(Protocol):
    async def append_canonical(self, message: CanonicalMessage) -> None: ...

    async def load_canonical(
        self, partition: SessionPartition, message_ids: Sequence[str]
    ) -> list[CanonicalMessage | None]: ...

    async def load_projection(self, partition: SessionPartition) -> ProjectionDocument | None: ...

    async def save_projection(
        self,
        partition: SessionPartition,
        projection: ProjectionDocument,
        todos: TodoProjection,
        expected_etag: str | None,
    ) -> ProjectionDocument: ...


class InMemoryProjectionRepository:
    def __init__(self) -> None:
        self._messages: dict[tuple[str, str, str, str], CanonicalMessage] = {}
        self._projections: dict[tuple[str, str, str], ProjectionDocument] = {}
        self._todos: dict[tuple[str, str, str], TodoProjection] = {}
        self._etag_counter = 0

    async def append_canonical(self, message: CanonicalMessage) -> None:
        key = (*self._key_from_values(message.tenant_id, message.owner_object_id, message.session_id), message.id)
        existing = self._messages.get(key)
        if existing is None:
            self._messages[key] = message
            return
        if existing != message:
            raise MessageConflict("canonical message conflict")

    async def load_canonical(
        self, partition: SessionPartition, message_ids: Sequence[str]
    ) -> list[CanonicalMessage | None]:
        partition_key = self._key(partition)
        return [self._messages.get((*partition_key, message_id)) for message_id in message_ids]

    async def load_projection(self, partition: SessionPartition) -> ProjectionDocument | None:
        return self._projections.get(self._key(partition))

    async def save_projection(
        self,
        partition: SessionPartition,
        projection: ProjectionDocument,
        todos: TodoProjection,
        expected_etag: str | None,
    ) -> ProjectionDocument:
        key = self._key(partition)
        existing = self._projections.get(key)
        if existing is None:
            if expected_etag is not None:
                raise ProjectionConflict("projection does not exist")
        elif expected_etag != existing.etag:
            raise ProjectionConflict("projection ETag conflict")
        self._etag_counter += 1
        saved = projection.model_copy(update={"etag": f'"{self._etag_counter}"'})
        self._projections[key] = saved
        self._todos[key] = todos
        return saved

    @staticmethod
    def _key(partition: SessionPartition) -> tuple[str, str, str]:
        return tuple(partition.values())  # type: ignore[return-value]

    @staticmethod
    def _key_from_values(tenant_id: object, owner_object_id: object, session_id: str) -> tuple[str, str, str]:
        return str(tenant_id), str(owner_object_id), session_id


class CosmosProjectionRepository:
    def __init__(self, container: ContainerProxy) -> None:
        self._container = container

    async def append_canonical(self, message: CanonicalMessage) -> None:
        body = message.model_dump(mode="json", by_alias=True)
        partition_key = self._partition_values(
            SessionPartition(
                tenant_id=UUID(message.tenant_id),
                owner_object_id=UUID(message.owner_object_id),
                session_id=message.session_id,
            )
        )
        try:
            await self._container.create_item(body=body)
            return
        except cosmos_exceptions.CosmosHttpResponseError as error:
            if error.status_code != 409:
                raise

        existing_raw = await self._container.read_item(item=message.id, partition_key=partition_key)
        existing = CanonicalMessage.model_validate(existing_raw)
        if existing != message:
            raise MessageConflict("canonical message conflict")

    async def load_canonical(
        self, partition: SessionPartition, message_ids: Sequence[str]
    ) -> list[CanonicalMessage | None]:
        partition_key = self._partition_values(partition)
        loaded: list[CanonicalMessage | None] = []
        for message_id in message_ids:
            try:
                raw = await self._container.read_item(item=message_id, partition_key=partition_key)
            except cosmos_exceptions.CosmosHttpResponseError as error:
                if error.status_code == 404:
                    loaded.append(None)
                    continue
                raise
            loaded.append(CanonicalMessage.model_validate(raw))
        return loaded

    async def load_projection(self, partition: SessionPartition) -> ProjectionDocument | None:
        partition_key = self._partition_values(partition)
        try:
            raw = await self._container.read_item(item="projection", partition_key=partition_key)
        except cosmos_exceptions.CosmosHttpResponseError as error:
            if error.status_code == 404:
                return None
            raise
        return ProjectionDocument.model_validate(raw)

    async def load_todos(self, partition: SessionPartition) -> TodoProjection | None:
        partition_key = self._partition_values(partition)
        try:
            raw = await self._container.read_item(item="todo-projection", partition_key=partition_key)
        except cosmos_exceptions.CosmosHttpResponseError as error:
            if error.status_code == 404:
                return None
            raise
        return TodoProjection.model_validate(raw)

    async def save_projection(
        self,
        partition: SessionPartition,
        projection: ProjectionDocument,
        todos: TodoProjection,
        expected_etag: str | None,
    ) -> ProjectionDocument:
        partition_key = self._partition_values(partition)
        projection_body = projection.model_dump(mode="json", by_alias=True)
        todo_body = todos.model_dump(mode="json", by_alias=True)
        operations: Sequence[tuple[str, tuple[object, ...]] | tuple[str, tuple[object, ...], dict[str, object]]]
        if expected_etag is None:
            operations = (
                ("create", (projection_body,)),
                ("create", (todo_body,)),
            )
        else:
            operations = (
                ("replace", ("projection", projection_body), {"if_match_etag": expected_etag}),
                ("replace", ("todo-projection", todo_body)),
            )
        try:
            await self._container.execute_item_batch(batch_operations=operations, partition_key=partition_key)
        except cosmos_exceptions.CosmosBatchOperationError as error:
            if error.status_code == 412:
                raise ProjectionConflict("projection ETag conflict") from error
            raise
        except cosmos_exceptions.CosmosHttpResponseError as error:
            if error.status_code == 412:
                raise ProjectionConflict("projection ETag conflict") from error
            raise

        saved = await self._container.read_item(item="projection", partition_key=partition_key)
        return ProjectionDocument.model_validate(saved)

    @staticmethod
    def _partition_values(partition: SessionPartition) -> list[str]:
        return partition.values()
