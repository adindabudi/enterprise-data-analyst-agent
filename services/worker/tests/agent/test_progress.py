from __future__ import annotations

from typing import Any

import pytest
from eda_runtime_state.events import ACTIVITY_EVENT, EventDraft
from eda_worker.agent.progress import MAX_PUBLISHED_TODOS, TodoUpdateMiddleware, ToolProgressMiddleware, current_todos


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
        await middleware.process(Context("query_graph", {"task_id": "task_12345678", "workspace": workspace_id}), _noop)

    # The specification forbids a target UUID reaching logs or telemetry.
    assert workspace_id not in caplog.records[-1].getMessage()


class TodoRecorder:
    def __init__(self, *, failing: bool = False) -> None:
        self.calls: list[tuple[str, tuple[dict[str, object], ...]]] = []
        self.failing = failing

    async def __call__(self, task_id: str, items: tuple[dict[str, object], ...]) -> None:
        self.calls.append((task_id, items))
        if self.failing:
            raise ConnectionError("redis unavailable")


class Session:
    def __init__(self, items: list[dict[str, object]]) -> None:
        self.state: dict[str, object] = {"todo": {"items": items, "next_id": len(items) + 1}}


class TodoContext(Context):
    def __init__(self, name: str, session: Session | None, task_id: str = "task_12345678") -> None:
        super().__init__(name, {"task_id": task_id})
        self.session = session


PLAN = [
    {
        "id": 1,
        "title": "Susun rencana dashboard okupansi",
        "description": "Tetapkan ruang lingkup.",
        "is_complete": True,
    },
    {"id": 2, "title": "Bangun dashboard interaktif", "description": None, "is_complete": False},
]


@pytest.mark.asyncio
async def test_a_plan_change_is_published_with_the_whole_current_plan() -> None:
    recorder = TodoRecorder()

    await TodoUpdateMiddleware(recorder).process(TodoContext("todos_add", Session(PLAN)), _noop)

    [(task_id, items)] = recorder.calls
    assert task_id == "task_12345678"
    assert items == (
        {
            "todoId": "1",
            "text": "Susun rencana dashboard okupansi",
            "description": "Tetapkan ruang lingkup.",
            "completed": True,
        },
        {"todoId": "2", "text": "Bangun dashboard interaktif", "description": None, "completed": False},
    )
    # The payload is exactly the contract's todo.updated event, so the live view can parse it.
    ACTIVITY_EVENT.validate_python(
        EventDraft(session_id="ses_1234567890abcdef", task_id=task_id, type="todo.updated", payload=list(items)).body()
        | {"sequence": 1}
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["todos_get_all", "todos_get_remaining", "execute_in_sandbox"])
async def test_reading_the_plan_or_using_other_tools_publishes_nothing(name: str) -> None:
    recorder = TodoRecorder()

    await TodoUpdateMiddleware(recorder).process(TodoContext(name, Session(PLAN)), _noop)

    assert recorder.calls == []


@pytest.mark.asyncio
async def test_a_failed_plan_change_publishes_nothing() -> None:
    recorder = TodoRecorder()

    async def failing() -> None:
        raise ValueError("items must contain at least one entry.")

    with pytest.raises(ValueError, match="at least one"):
        await TodoUpdateMiddleware(recorder).process(TodoContext("todos_complete", Session(PLAN)), failing)
    assert recorder.calls == []


@pytest.mark.asyncio
async def test_a_reporting_failure_never_fails_the_tool_call() -> None:
    recorder = TodoRecorder(failing=True)

    await TodoUpdateMiddleware(recorder).process(TodoContext("todos_remove", Session(PLAN)), _noop)

    assert len(recorder.calls) == 1


def test_the_published_plan_is_bounded_and_skips_malformed_items() -> None:
    items: list[dict[str, object]] = [
        {"id": 1, "title": "  " + "x" * 600, "description": "d" * 2500, "is_complete": False},
        {"id": "2", "title": "string id"},
        {"id": 3, "title": "   "},
        {"id": 4, "title": "Kept", "is_complete": "yes"},
        *({"id": index, "title": f"Item {index}"} for index in range(5, 80)),
    ]

    published = current_todos(Session(items))

    assert published is not None
    assert len(published) <= MAX_PUBLISHED_TODOS
    assert published[0]["text"] == "x" * 500 and published[0]["description"] == "d" * 2000
    assert [item["todoId"] for item in published[:2]] == ["1", "4"]
    assert published[1]["completed"] is False
    assert current_todos(None) is None
    assert current_todos(Session([])) == ()
