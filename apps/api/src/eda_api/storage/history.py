from __future__ import annotations

from datetime import datetime
from typing import Literal, Protocol

from azure.cosmos.aio import ContainerProxy
from eda_api.auth.models import Principal
from eda_api.chat.provenance import ChatQueryRecord, legacy_query_steps, read_query_record
from eda_contracts.tasks import TaskStatus
from eda_runtime_state.messages import CanonicalMessage
from eda_runtime_state.models import TaskPartition, TaskRecord
from pydantic import BaseModel, ConfigDict, Field

from .models import camel_case


class HistoryView(BaseModel):
    model_config = ConfigDict(alias_generator=camel_case, populate_by_name=True, extra="ignore", frozen=True)


class SessionMessageView(HistoryView):
    message_id: str
    role: Literal["user", "assistant"]
    text: str
    created_at: datetime
    task_id: str | None = None
    steps: list[dict[str, str]] = Field(default_factory=list[dict[str, str]])


class SessionTaskView(HistoryView):
    task_id: str
    status: TaskStatus
    source_message_id: str | None
    final_message_id: str | None
    created_at: datetime
    updated_at: datetime


class SessionHistory(HistoryView):
    messages: tuple[SessionMessageView, ...] = ()
    tasks: tuple[SessionTaskView, ...] = ()


class SessionHistoryReader(Protocol):
    async def read_history(self, principal: Principal, session_id: str) -> SessionHistory: ...


class CosmosSessionHistoryReader:
    def __init__(self, workspace: ContainerProxy) -> None:
        self._workspace = workspace

    async def read_history(self, principal: Principal, session_id: str) -> SessionHistory:
        partition = TaskPartition(
            tenant_id=principal.tenant_id, owner_object_id=principal.owner_object_id, session_id=session_id
        )
        records = self._workspace.query_items(
            query="SELECT * FROM c WHERE c.recordType IN ('message', 'task', 'chatQuery', 'interactiveSession')",
            partition_key=partition.values(),
        )
        messages: list[SessionMessageView] = []
        tasks: list[SessionTaskView] = []
        queries: list[ChatQueryRecord] = []
        legacy_state: object = None
        async for record in records:
            if [record.get("tenantId"), record.get("ownerObjectId"), record.get("sessionId")] != partition.values():
                continue
            if record.get("recordType") == "message":
                message = CanonicalMessage.model_validate(record)
                messages.append(
                    SessionMessageView(
                        message_id=message.id,
                        role=message.role,
                        text=message.text,
                        created_at=message.created_at,
                        task_id=message.task_id,
                    )
                )
            elif record.get("recordType") == "task":
                task = TaskRecord.model_validate(record)
                tasks.append(
                    SessionTaskView(
                        task_id=task.id,
                        status=task.status,
                        source_message_id=task.source_message_id,
                        final_message_id=task.final_message_id,
                        created_at=task.created_at,
                        updated_at=task.updated_at,
                    )
                )
            elif record.get("recordType") == "chatQuery":
                queries.append(read_query_record(record, partition))
            elif record.get("recordType") == "interactiveSession" and record.get("id") == "interactive-session":
                legacy_state = record.get("state")
        user_ids = {message.message_id for message in messages if message.role == "user"}
        steps = legacy_query_steps(legacy_state, user_ids)
        durable: dict[str, list[dict[str, str]]] = {}
        for query in sorted(queries, key=lambda item: (item.step.started_at, item.sequence, item.id)):
            if query.source_message_id in user_ids:
                durable.setdefault(query.source_message_id, []).append(query.step.event_data())
        steps.update(durable)
        messages = [message.model_copy(update={"steps": steps.get(message.message_id, [])}) for message in messages]
        return SessionHistory(
            messages=tuple(sorted(messages, key=lambda item: (item.created_at, item.message_id))),
            tasks=tuple(sorted(tasks, key=lambda item: (item.created_at, item.task_id))),
        )
