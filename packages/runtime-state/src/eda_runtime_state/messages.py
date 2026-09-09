from __future__ import annotations

import base64
import hashlib
from datetime import UTC, datetime
from typing import Literal, Protocol
from uuid import UUID

from azure.cosmos.aio import ContainerProxy
from azure.cosmos.exceptions import CosmosHttpResponseError, CosmosResourceNotFoundError
from pydantic import AliasChoices, Field

from .models import RuntimeModel, TaskPartition


class MessageConflict(ValueError):
    pass


class CanonicalMessage(RuntimeModel):
    id: str = Field(pattern=r"^msg_[A-Za-z0-9_-]{8,}$")
    record_type: Literal["message"] = Field(
        default="message",
        serialization_alias="recordType",
        validation_alias=AliasChoices("recordType", "record_type"),
    )
    tenant_id: str = Field(
        serialization_alias="tenantId",
        validation_alias=AliasChoices("tenantId", "tenant_id"),
    )
    owner_object_id: str = Field(
        serialization_alias="ownerObjectId",
        validation_alias=AliasChoices("ownerObjectId", "owner_object_id"),
    )
    session_id: str = Field(
        serialization_alias="sessionId",
        validation_alias=AliasChoices("sessionId", "session_id"),
    )
    role: Literal["user", "assistant"]
    text: str = Field(min_length=1, max_length=1000000)
    created_at: datetime = Field(
        serialization_alias="createdAt",
        validation_alias=AliasChoices("createdAt", "created_at"),
    )
    task_id: str | None = Field(
        default=None,
        serialization_alias="taskId",
        validation_alias=AliasChoices("taskId", "task_id"),
    )


class MessageRepository(Protocol):
    async def append_user(self, partition: TaskPartition, text: str, idempotency_key: str) -> CanonicalMessage: ...

    async def append_canonical(self, message: CanonicalMessage) -> None: ...

    async def get_owned(self, partition: TaskPartition, message_id: str) -> CanonicalMessage | None: ...


def deterministic_message_id(partition: TaskPartition, idempotency_key: str) -> str:
    material = "\0".join([*partition.values(), idempotency_key]).encode()
    digest = base64.urlsafe_b64encode(hashlib.sha256(material).digest()).decode().rstrip("=")
    return f"msg_{digest[:32]}"


class InMemoryMessageRepository:
    def __init__(self) -> None:
        self._messages: dict[tuple[str, str, str, str], CanonicalMessage] = {}

    async def append_user(self, partition: TaskPartition, text: str, idempotency_key: str) -> CanonicalMessage:
        message_id = deterministic_message_id(partition, idempotency_key)
        message = CanonicalMessage(
            id=message_id,
            tenant_id=str(partition.tenant_id),
            owner_object_id=str(partition.owner_object_id),
            session_id=partition.session_id,
            role="user",
            text=text,
            created_at=datetime.now(UTC),
            task_id=None,
        )
        partition_values = partition.values()
        key = (partition_values[0], partition_values[1], partition_values[2], message_id)
        existing = self._messages.get(key)
        if existing is None:
            self._messages[key] = message
            return message
        if existing.text != text:
            raise MessageConflict("canonical message conflict")
        return existing

    async def get_owned(self, partition: TaskPartition, message_id: str) -> CanonicalMessage | None:
        partition_values = partition.values()
        key = (partition_values[0], partition_values[1], partition_values[2], message_id)
        return self._messages.get(key)

    async def append_canonical(self, message: CanonicalMessage) -> None:
        key = (message.tenant_id, message.owner_object_id, message.session_id, message.id)
        existing = self._messages.get(key)
        if existing is None:
            self._messages[key] = message
            return
        if existing != message:
            raise MessageConflict("canonical message conflict")


class CosmosMessageRepository:
    def __init__(self, container: ContainerProxy) -> None:
        self._container = container

    async def append_user(self, partition: TaskPartition, text: str, idempotency_key: str) -> CanonicalMessage:
        message_id = deterministic_message_id(partition, idempotency_key)
        message = CanonicalMessage(
            id=message_id,
            tenant_id=str(partition.tenant_id),
            owner_object_id=str(partition.owner_object_id),
            session_id=partition.session_id,
            role="user",
            text=text,
            created_at=datetime.now(UTC),
            task_id=None,
        )
        create_error: CosmosHttpResponseError | None = None
        try:
            await self._container.create_item(body=message.model_dump(mode="json", by_alias=True))
            return message
        except CosmosHttpResponseError as error:
            if error.status_code != 409:
                raise
            create_error = error

        existing = await self.get_owned(partition, message_id)
        if existing is None:
            raise MessageConflict("canonical message conflict") from create_error
        if existing.text != text:
            raise MessageConflict("canonical message conflict")
        return existing

    async def get_owned(self, partition: TaskPartition, message_id: str) -> CanonicalMessage | None:
        try:
            raw = await self._container.read_item(item=message_id, partition_key=partition.values())
        except CosmosResourceNotFoundError:
            return None
        except CosmosHttpResponseError as error:
            if error.status_code == 404:
                return None
            raise
        return CanonicalMessage.model_validate(raw)

    async def append_canonical(self, message: CanonicalMessage) -> None:
        partition = TaskPartition(
            tenant_id=UUID(message.tenant_id),
            owner_object_id=UUID(message.owner_object_id),
            session_id=message.session_id,
        )
        try:
            await self._container.create_item(body=message.model_dump(mode="json", by_alias=True))
            return
        except CosmosHttpResponseError as error:
            if error.status_code != 409:
                raise
        existing = await self.get_owned(partition, message.id)
        if existing != message:
            raise MessageConflict("canonical message conflict")
