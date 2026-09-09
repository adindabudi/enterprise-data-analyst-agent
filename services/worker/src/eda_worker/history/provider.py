from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, cast

from agent_framework import AgentSession, HistoryProvider, Message, SessionContext, SupportsAgentRun

from .models import ProjectionDocument, SessionPartition, TodoProjection
from .repository import ProjectionConflict, ProjectionRepository

# agent_framework._ensure_message_ids labels an unidentified message "msg_<index>". That index is
# only stable within one attempt, and a stream failure re-runs the turn, so it is not an identity.
# Our own ids carry at least eight characters after the prefix, so no index can reach this shape.
_POSITIONAL_ID = re.compile(r"msg_\d{1,7}")


class ProjectionCorruption(ValueError):
    pass


class ProjectionWriteConflict(RuntimeError):
    pass


class ProjectionHistoryProvider(HistoryProvider):
    def __init__(self, repository: ProjectionRepository) -> None:
        super().__init__("projection", load_messages=True, store_inputs=True, store_outputs=True)
        self.repository = repository

    async def get_messages(
        self,
        session_id: str | None,
        *,
        state: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> list[Message]:
        del kwargs
        partition = _partition_from_state(state, session_id)
        projection = await self.repository.load_projection(partition)
        if projection is None:
            return []
        return [_deserialize_message(message) for message in projection.messages]

    async def save_messages(
        self,
        session_id: str | None,
        messages: Sequence[Message],
        *,
        state: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        del kwargs
        partition = _partition_from_state(state, session_id)
        session = await self.restore_session(partition)
        await self._save(partition, session, messages)

    async def after_run(
        self,
        *,
        agent: SupportsAgentRun,
        session: AgentSession,
        context: SessionContext,
        state: dict[str, Any],
    ) -> None:
        del agent
        partition = _partition_from_state(state, context.session_id)
        messages: list[Message] = []
        messages.extend(self._get_context_messages_to_store(context))
        if self.store_inputs:
            messages.extend(context.input_messages)
        if self.store_outputs and context.response and context.response.messages:
            messages.extend(context.response.messages)
        await self._save(partition, session, messages)

    async def restore_session(self, partition: SessionPartition) -> AgentSession:
        projection = await self.repository.load_projection(partition)
        if projection is None:
            session = AgentSession(session_id=partition.session_id)
        else:
            session_document = dict(projection.agent_session)
            session_document.setdefault("type", "session")
            session_document.setdefault("session_id", partition.session_id)
            session_document.setdefault("service_session_id", None)
            session_document.setdefault("state", {})
            session = AgentSession.from_dict(session_document)
            if session.service_session_id is not None:
                raise ProjectionCorruption("projection contains a provider conversation ID")
            if session.session_id != partition.session_id:
                raise ProjectionCorruption("projection belongs to another session")
        session.state.setdefault(self.source_id, {})["partition"] = partition.model_dump(mode="json")
        return session

    async def _save(
        self,
        partition: SessionPartition,
        session: AgentSession,
        incoming: Sequence[Message],
    ) -> None:
        sanitized = _sanitize_and_identify(incoming, partition.session_id)
        for attempt in range(2):
            existing = await self.repository.load_projection(partition)
            existing_messages = (
                [_deserialize_message(message) for message in existing.messages] if existing is not None else []
            )
            merged = _merge_messages(existing_messages, sanitized)
            session_document = session.to_dict()
            session_document["service_session_id"] = None
            serialized = tuple(message.to_dict() for message in merged)
            now = datetime.now(UTC)
            projection = ProjectionDocument(
                tenant_id=partition.tenant_id,
                owner_object_id=partition.owner_object_id,
                session_id=partition.session_id,
                projection_version=(existing.projection_version + 1) if existing else 1,
                summary_version=existing.summary_version if existing else 0,
                messages=serialized,
                agent_session=session_document,
                projected_tokens=sum(_message_token_estimate(message) for message in merged),
                source_message_ids=tuple(cast(str, message.message_id) for message in merged),
                updated_at=now,
            )
            session_state: object = session_document.get("state", {})
            todo_state: object = (
                cast(dict[str, object], session_state).get("todo", {}) if isinstance(session_state, dict) else {}
            )
            todo_items: object = (
                cast(dict[str, object], todo_state).get("items", []) if isinstance(todo_state, dict) else []
            )
            if not isinstance(todo_items, list):
                raise ProjectionCorruption("todo provider state is malformed")
            todo_values = cast(list[object], todo_items)
            if not all(isinstance(item, dict) for item in todo_values):
                raise ProjectionCorruption("todo provider state is malformed")
            todos = TodoProjection(
                tenant_id=partition.tenant_id,
                owner_object_id=partition.owner_object_id,
                session_id=partition.session_id,
                items=tuple(cast(list[dict[str, Any]], todo_values)),
                updated_at=now,
            )
            try:
                await self.repository.save_projection(
                    partition,
                    projection,
                    todos,
                    existing.etag if existing else None,
                )
                return
            except ProjectionConflict as error:
                if attempt == 1:
                    raise ProjectionWriteConflict("projection changed twice during one model call") from error


def sanitize_message(message: Message) -> Message | None:
    contents = [content for content in message.contents if content.type not in {"reasoning", "text_reasoning"}]
    if not contents:
        return None
    return Message(
        role=message.role,
        contents=contents,
        author_name=message.author_name,
        message_id=message.message_id,
        additional_properties={
            key: value
            for key, value in message.additional_properties.items()
            if key not in {"reasoning", "encrypted_content", "protected_data"}
        },
    )


def _sanitize_and_identify(messages: Sequence[Message], session_id: str) -> list[Message]:
    sanitized: list[Message] = []
    for ordinal, message in enumerate(messages):
        clean = sanitize_message(message)
        if clean is None:
            continue
        if clean.message_id is None or _POSITIONAL_ID.fullmatch(clean.message_id):
            identity = json.dumps(clean.to_dict(), ensure_ascii=True, separators=(",", ":"), sort_keys=True)
            digest = hashlib.sha256(f"{session_id}|{clean.role}|{identity}|{ordinal}".encode()).hexdigest()
            clean = Message(
                role=clean.role,
                contents=clean.contents,
                author_name=clean.author_name,
                message_id=f"msg_{digest}",
                additional_properties=clean.additional_properties,
            )
        sanitized.append(clean)
    return sanitized


def _merge_messages(existing: Sequence[Message], incoming: Sequence[Message]) -> list[Message]:
    merged: list[Message] = []
    by_id: dict[str, str] = {}
    for message in [*existing, *incoming]:
        message_id = message.message_id
        if message_id is None:
            raise ProjectionCorruption("projected message is missing its stable ID")
        canonical = json.dumps(message.to_dict(), ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        prior = by_id.get(message_id)
        if prior is not None:
            if prior != canonical:
                raise ProjectionCorruption(f"projected message ID {message_id} has conflicting content")
            continue
        by_id[message_id] = canonical
        merged.append(message)
    return merged


def _partition_from_state(state: dict[str, Any] | None, session_id: str | None) -> SessionPartition:
    raw_partition = state.get("partition") if state is not None else None
    if not isinstance(raw_partition, dict):
        raise ValueError("projection history requires a trusted session partition")
    partition = SessionPartition.model_validate(raw_partition)
    if session_id is not None and partition.session_id != session_id:
        raise ValueError("projection partition belongs to another session")
    return partition


def _deserialize_message(document: dict[str, Any]) -> Message:
    if "contents" in document:
        return Message.from_dict(document)
    text = document.get("text")
    role = document.get("role")
    if not isinstance(text, str) or not isinstance(role, str):
        raise ProjectionCorruption("projected message is malformed")
    message_id = document.get("message_id")
    return Message(role=role, contents=[text], message_id=message_id if isinstance(message_id, str) else None)


def _message_token_estimate(message: Message) -> int:
    return max(1, len(json.dumps(message.to_dict(), ensure_ascii=True, separators=(",", ":"))) // 4)


class ProjectionProvider:
    def __init__(self, repository: ProjectionRepository, partition: SessionPartition) -> None:
        self.repository = repository
        self.partition = partition

    async def after_run(self, context: list[dict[str, Any]]) -> None:
        existing = await self.repository.load_projection(self.partition)
        messages = _sanitize_messages(context)
        if existing is not None:
            messages = _deduplicate(existing.messages, messages)
        projection = ProjectionDocument(
            tenant_id=self.partition.tenant_id,
            owner_object_id=self.partition.owner_object_id,
            session_id=self.partition.session_id,
            projection_version=(existing.projection_version + 1) if existing else 1,
            summary_version=existing.summary_version if existing else 0,
            messages=tuple(messages),
            agent_session={"service_session_id": None},
            projected_tokens=sum(_tokens(message) for message in messages),
            source_message_ids=tuple(message["message_id"] for message in messages),
            updated_at=datetime.now(UTC),
        )
        todos = TodoProjection(
            tenant_id=self.partition.tenant_id,
            owner_object_id=self.partition.owner_object_id,
            session_id=self.partition.session_id,
            items=(),
            updated_at=datetime.now(UTC),
        )
        await self.repository.save_projection(self.partition, projection, todos, existing.etag if existing else None)


def _sanitize_messages(context: list[dict[str, Any]]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for item in context:
        message_id = item.get("message_id")
        text = item.get("text")
        role = item.get("role")
        if not isinstance(message_id, str) or not isinstance(text, str) or not isinstance(role, str):
            continue
        messages.append({"message_id": message_id, "role": role, "text": text, "tokens": _tokens(item)})
    return messages


def _deduplicate(existing: tuple[dict[str, Any], ...], current: list[dict[str, Any]]) -> list[dict[str, Any]]:
    values: dict[str, dict[str, Any]] = {}
    for message in [*existing, *current]:
        message_id = message.get("message_id")
        if isinstance(message_id, str):
            values[message_id] = message
    return list(values.values())


def _tokens(message: dict[str, Any]) -> int:
    value = message.get("tokens")
    if isinstance(value, int) and value >= 0:
        return value
    text = message.get("text")
    return max(1, len(text) // 4) if isinstance(text, str) else 0
