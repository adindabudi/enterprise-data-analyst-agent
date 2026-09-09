from __future__ import annotations

from typing import Protocol

from eda_runtime_state.models import TaskRecord

from .models import SessionPartition, TodoProjection


class TodoProjectionSource(Protocol):
    async def load_todos(self, partition: SessionPartition) -> TodoProjection | None: ...


class SessionOpenTodos:
    """Reads back the plan the agent wrote, so a run cannot quietly abandon it."""

    def __init__(self, source: TodoProjectionSource) -> None:
        self._source = source

    async def open_todos(self, task: TaskRecord) -> tuple[str, ...]:
        partition = SessionPartition(
            tenant_id=task.tenant_id,
            owner_object_id=task.owner_object_id,
            session_id=task.session_id,
        )
        projection = await self._source.load_todos(partition)
        if projection is None:
            return ()
        titles: list[str] = []
        for entry in projection.items:
            if entry.get("is_complete"):
                continue
            title = entry.get("title")
            if isinstance(title, str) and title.strip():
                titles.append(title.strip())
        return tuple(titles)
