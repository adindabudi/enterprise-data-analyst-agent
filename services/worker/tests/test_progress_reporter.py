from __future__ import annotations

from typing import Any

import pytest
from eda_runtime_state.events import EventDraft
from eda_worker.runtime import create_progress_reporter
from redis.exceptions import RedisError


class Task:
    id = "task_12345678"
    session_id = "ses_12345678"


class Repository:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    async def resolve_task(self, task_id: str) -> Any:
        if self.error is not None:
            raise self.error
        return Task()


class EventStore:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.drafts: list[EventDraft] = []

    async def append(self, draft: EventDraft) -> object:
        if self.error is not None:
            raise self.error
        self.drafts.append(draft)
        return None


@pytest.mark.asyncio
async def test_a_milestone_reaches_the_event_stream() -> None:
    store = EventStore()
    report = create_progress_reporter(Repository(), store)  # type: ignore[arg-type]

    await report("task_12345678", "Running code", "Started.", "running")

    assert store.drafts[0].type == "analysis_progress"
    assert store.drafts[0].payload == {"milestone": "Running code", "detail": "Started.", "state": "running"}


@pytest.mark.parametrize(
    "failure",
    [RedisError("stream unavailable"), ConnectionError("redis is unreachable")],
)
@pytest.mark.asyncio
async def test_a_dropped_event_never_reaches_the_caller(failure: Exception) -> None:
    report = create_progress_reporter(Repository(), EventStore(failure))  # type: ignore[arg-type]

    await report("task_12345678", "Running code", "Started.", "running")


@pytest.mark.asyncio
async def test_a_broken_lookup_never_reaches_the_caller(caplog: pytest.LogCaptureFixture) -> None:
    # This runs around every tool call. Raised from inside the except branch it replaced the
    # tool's own failure, and raised from the success branch it failed a tool that had worked.
    report = create_progress_reporter(Repository(RuntimeError("cosmos is unreachable")), EventStore())  # type: ignore[arg-type]

    await report("task_12345678", "Running code", "Started.", "running")

    assert "progress reporting failed" in caplog.text
