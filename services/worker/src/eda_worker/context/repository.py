from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from eda_contracts.tasks import TaskStatus
from eda_runtime_state.messages import CanonicalMessage
from eda_runtime_state.models import TaskRecord
from eda_worker.history.models import SessionPartition

from .task_state import ContextSnapshot, QueryResultContextRef


class RuntimeTaskReader(Protocol):
    async def resolve_task(self, task_id: str) -> TaskRecord | None: ...


class CanonicalMessageReader(Protocol):
    async def load_canonical(
        self,
        partition: SessionPartition,
        message_ids: Sequence[str],
    ) -> list[CanonicalMessage | None]: ...


class RuntimeTaskStateRepository:
    def __init__(self, runtime: RuntimeTaskReader, messages: CanonicalMessageReader) -> None:
        self._runtime = runtime
        self._messages = messages

    async def context_snapshot(self, task_id: str) -> ContextSnapshot:
        task = await self._runtime.resolve_task(task_id)
        if task is None:
            raise ValueError("task is unavailable for context hydration")
        requirements: tuple[str, ...] = ()
        if task.source_message_id is not None:
            partition = SessionPartition(
                tenant_id=task.tenant_id,
                owner_object_id=task.owner_object_id,
                session_id=task.session_id,
            )
            messages = await self._messages.load_canonical(partition, [task.source_message_id])
            if len(messages) != 1 or messages[0] is None or messages[0].role != "user":
                raise ValueError("task source message is unavailable for context hydration")
            requirements = (messages[0].text,)
        return ContextSnapshot(
            confirmed_requirements=requirements,
            query_results=tuple(
                QueryResultContextRef(
                    artifact_id=stored.artifact_id,
                    version=stored.version,
                    kind=stored.kind,
                    sha256=stored.sha256,
                    display_name=stored.display_name,
                    query=stored.query,
                    row_count=stored.row_count,
                    source_alias=stored.source_alias,
                )
                for stored in task.query_results
            ),
            input_artifacts=task.input_artifacts,
            required_outputs=task.required_outputs,
            workflow_phase=task.status.value,
            pending_auth=task.status is TaskStatus.BLOCKED_AUTH,
        )
