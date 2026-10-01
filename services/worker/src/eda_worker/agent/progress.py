from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from typing import cast

from agent_framework import FunctionInvocationContext, FunctionMiddleware
from eda_worker.tools.capabilities import ProgressReporter

logger = logging.getLogger(__name__)

# The harness's todo tools, which change the plan the user sees.
TODO_MUTATIONS = frozenset({"todos_add", "todos_complete", "todos_remove"})
MAX_PUBLISHED_TODOS = 50
MAX_TODO_TEXT_CHARS = 500
MAX_TODO_DESCRIPTION_CHARS = 2000

# Receives the task and the whole current plan, as `todo.updated` payload items.
TodoReporter = Callable[[str, tuple[dict[str, object], ...]], Awaitable[None]]

_MILESTONES = {
    "execute_in_sandbox": "Running code",
    "inspect_artifact": "Inspecting data",
    "validate_artifact": "Validating output",
    "publish_artifact": "Publishing output",
    "query_graph": "Querying the source",
    "query_timeseries": "Querying the source",
}


class ToolProgressMiddleware(FunctionMiddleware):
    """Reports every tool call so a long run shows movement instead of a silent spinner."""

    def __init__(self, progress: ProgressReporter) -> None:
        self._progress = progress

    async def process(self, context: FunctionInvocationContext, call_next: Callable[[], Awaitable[None]]) -> None:
        task_id = self._task_id(context)
        name = getattr(getattr(context, "function", None), "name", None)
        if task_id is None or not isinstance(name, str):
            await call_next()
            return
        milestone = _MILESTONES.get(name, f"Using {name}")
        await self._progress(task_id, milestone, f"Started {name}.", "running")
        started = time.monotonic()
        try:
            await call_next()
        except Exception:
            self._record(name, task_id, "failed", started)
            await self._progress(task_id, milestone, f"{name} failed.", "failed")
            raise
        self._record(name, task_id, "ok", started)
        await self._progress(task_id, milestone, f"Finished {name}.", "completed")

    @staticmethod
    def _record(name: str, task_id: str, outcome: str, started: float) -> None:
        """Names and outcomes only. Arguments and results carry source data and identifiers.

        Without this a run that produced nothing leaves no trace of what it tried.
        """
        logger.info(
            "tool call name=%s task=%s outcome=%s ms=%d",
            name,
            task_id,
            outcome,
            int((time.monotonic() - started) * 1000),
        )

    @staticmethod
    def _task_id(context: FunctionInvocationContext) -> str | None:
        raw: object = getattr(context, "kwargs", None)
        if not isinstance(raw, dict):
            return None
        task_id: object = cast("dict[str, object]", raw).get("task_id")
        return task_id if isinstance(task_id, str) and task_id.startswith("task_") else None


class TodoUpdateMiddleware(FunctionMiddleware):
    """Publishes the plan as soon as the agent changes it.

    The plan is otherwise persisted only with the run's history, after the whole run, so the user
    saw it for the first time already finished.
    """

    def __init__(self, report: TodoReporter) -> None:
        self._report = report

    async def process(self, context: FunctionInvocationContext, call_next: Callable[[], Awaitable[None]]) -> None:
        await call_next()
        name = getattr(getattr(context, "function", None), "name", None)
        if name not in TODO_MUTATIONS:
            return
        task_id = ToolProgressMiddleware._task_id(context)  # pyright: ignore[reportPrivateUsage]
        items = current_todos(getattr(context, "session", None))
        if task_id is None or items is None:
            return
        try:
            await self._report(task_id, items)
        except Exception:
            # The plan is shown late rather than failing a tool call that succeeded.
            logger.exception("todo update could not be reported for task %s", task_id)


def current_todos(session: object) -> tuple[dict[str, object], ...] | None:
    """The harness's todo list from session state, shaped as `todo.updated` payload items."""
    state: object = getattr(session, "state", None)
    if not isinstance(state, dict):
        return None
    todo: object = cast("dict[str, object]", state).get("todo")
    if not isinstance(todo, dict):
        return None
    raw_items: object = cast("dict[str, object]", todo).get("items")
    if not isinstance(raw_items, list):
        return None
    items: list[dict[str, object]] = []
    for raw in cast("list[object]", raw_items)[:MAX_PUBLISHED_TODOS]:
        if not isinstance(raw, dict):
            continue
        entry = cast("dict[str, object]", raw)
        todo_id, title, description = entry.get("id"), entry.get("title"), entry.get("description")
        if not isinstance(todo_id, int) or not isinstance(title, str) or not title.strip():
            continue
        items.append(
            {
                "todoId": str(todo_id),
                "text": title.strip()[:MAX_TODO_TEXT_CHARS],
                "description": description.strip()[:MAX_TODO_DESCRIPTION_CHARS]
                if isinstance(description, str) and description.strip()
                else None,
                "completed": entry.get("is_complete") is True,
            }
        )
    return tuple(items)
