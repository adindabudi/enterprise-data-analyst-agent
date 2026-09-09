from __future__ import annotations

from typing import Any

import pytest
from eda_worker.agent.progress import ToolProgressMiddleware


class Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, str | None, str]] = []

    async def __call__(self, task_id: str, milestone: str, detail: str | None, state: str) -> None:
        self.events.append((task_id, milestone, detail, state))


class Function:
    def __init__(self, name: str) -> None:
        self.name = name


class Context:
    def __init__(self, name: str, kwargs: dict[str, Any] | None) -> None:
        self.function = Function(name)
        self.kwargs = kwargs


async def _noop() -> None:
    return None


@pytest.mark.asyncio
async def test_a_tool_call_is_announced_before_and_after_it_runs() -> None:
    recorder = Recorder()
    middleware = ToolProgressMiddleware(recorder)

    await middleware.process(Context("execute_in_sandbox", {"task_id": "task_12345678"}), _noop)

    assert [(milestone, detail, state) for _, milestone, detail, state in recorder.events] == [
        ("Running code", "Started execute_in_sandbox.", "running"),
        ("Running code", "Finished execute_in_sandbox.", "completed"),
    ]
    assert {task_id for task_id, _, _, _ in recorder.events} == {"task_12345678"}


@pytest.mark.asyncio
async def test_an_unknown_tool_still_reports_movement() -> None:
    recorder = Recorder()
    middleware = ToolProgressMiddleware(recorder)

    await middleware.process(Context("some_new_tool", {"task_id": "task_12345678"}), _noop)

    assert recorder.events[0][1] == "Using some_new_tool"


@pytest.mark.asyncio
async def test_a_failing_tool_still_reports_that_it_stopped() -> None:
    recorder = Recorder()
    middleware = ToolProgressMiddleware(recorder)

    async def failing() -> None:
        raise RuntimeError("tool exploded")

    with pytest.raises(RuntimeError):
        await middleware.process(Context("publish_artifact", {"task_id": "task_12345678"}), failing)

    assert recorder.events[-1][2] == "publish_artifact failed."
    # Reported as completed, this rendered a failed tool as a finished activity.
    assert recorder.events[-1][3] == "failed"


@pytest.mark.asyncio
async def test_a_call_without_a_task_is_left_alone() -> None:
    recorder = Recorder()
    middleware = ToolProgressMiddleware(recorder)
    calls: list[int] = []

    async def counted() -> None:
        calls.append(1)

    await middleware.process(Context("execute_in_sandbox", None), counted)
    await middleware.process(Context("execute_in_sandbox", {"task_id": 7}), counted)

    assert recorder.events == []
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_every_tool_call_leaves_a_line_to_read_afterwards(
    caplog: pytest.LogCaptureFixture,
) -> None:
    middleware = ToolProgressMiddleware(Recorder())

    with caplog.at_level("INFO", logger="eda_worker.agent.progress"):
        await middleware.process(Context("execute_in_sandbox", {"task_id": "task_12345678"}), _noop)

    # A run that produced nothing has to leave evidence of what it tried.
    message = caplog.records[-1].getMessage()
    assert "name=execute_in_sandbox" in message
    assert "task=task_12345678" in message
    assert "outcome=ok" in message


@pytest.mark.asyncio
async def test_a_failed_tool_call_is_recorded_as_failed(caplog: pytest.LogCaptureFixture) -> None:
    middleware = ToolProgressMiddleware(Recorder())

    async def boom() -> None:
        raise RuntimeError("the sandbox refused")

    with caplog.at_level("INFO", logger="eda_worker.agent.progress"), pytest.raises(RuntimeError):
        await middleware.process(Context("publish_artifact", {"task_id": "task_12345678"}), boom)

    assert "outcome=failed" in caplog.records[-1].getMessage()


@pytest.mark.asyncio
async def test_no_argument_or_result_reaches_the_log(caplog: pytest.LogCaptureFixture) -> None:
    middleware = ToolProgressMiddleware(Recorder())
    workspace_id = "b82afbde-8304-44c0-ac94-3cf69f6da909"

    with caplog.at_level("INFO", logger="eda_worker.agent.progress"):
        await middleware.process(
            Context("query_fabric", {"task_id": "task_12345678", "workspace": workspace_id}), _noop
        )

    # The specification forbids a target UUID reaching logs or telemetry.
    assert workspace_id not in caplog.records[-1].getMessage()
