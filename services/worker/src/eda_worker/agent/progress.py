from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from typing import cast

from agent_framework import FunctionInvocationContext, FunctionMiddleware
from eda_worker.tools.capabilities import ProgressReporter

logger = logging.getLogger(__name__)

_MILESTONES = {
    "execute_in_sandbox": "Running code",
    "inspect_artifact": "Inspecting data",
    "validate_artifact": "Validating output",
    "publish_artifact": "Publishing output",
    "query_fabric": "Querying the source",
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
