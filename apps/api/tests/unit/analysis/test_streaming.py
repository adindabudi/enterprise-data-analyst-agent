"""Live answer text: batched, superseded when it can no longer be the answer, and never fatal."""

from __future__ import annotations

import pytest
from eda_api.analysis.streaming import MAX_DELTA_CHARS, MAX_PUBLISH_FAILURES, TaskStreams
from eda_runtime_state.events import EventDraft

TASK = "task_stream000001"
SESSION = "ses_stream0000000001"
ATTEMPT = "run_" + "a" * 32


class Events:
    def __init__(self, *, failing: bool = False) -> None:
        self.drafts: list[EventDraft] = []
        self.failing = failing
        self.attempts = 0

    async def append(self, draft: EventDraft) -> object:
        self.attempts += 1
        if self.failing:
            raise ConnectionError("redis unavailable")
        self.drafts.append(draft)
        return draft


class Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


def _streams(events: Events, **kwargs: object) -> TaskStreams:
    streams = TaskStreams(events, **kwargs)  # type: ignore[arg-type]
    streams.open(TASK, session_id=SESSION, attempt_id=ATTEMPT)
    return streams


def _deltas(events: Events) -> list[tuple[str | None, int | None, str]]:
    return [
        (draft.response_attempt_id, draft.attempt_sequence, str(draft.payload["delta"]))  # type: ignore[index]
        for draft in events.drafts
        if draft.type == "message.delta"
    ]


@pytest.mark.asyncio
async def test_tokens_are_batched_and_flushed_before_publication() -> None:
    events = Events()
    clock = Clock()
    streams = _streams(events, batch_chars=10, batch_seconds=5.0, clock=clock)
    sink = streams.sink_for(TASK)
    assert sink is not None

    for token in ("Tot", "al ", "is ", "42."):
        await sink(token)
    assert _deltas(events) == [(f"{ATTEMPT}.0", 1, "Total is 42.")]

    await sink("!")
    assert len(_deltas(events)) == 1
    await sink.flush()
    assert _deltas(events)[-1] == (f"{ATTEMPT}.0", 2, "!")


@pytest.mark.asyncio
async def test_a_slow_stream_is_flushed_by_time_not_only_by_size() -> None:
    events = Events()
    clock = Clock()
    streams = _streams(events, batch_chars=1_000, batch_seconds=0.25, clock=clock)
    sink = streams.sink_for(TASK)
    assert sink is not None

    await sink("first")
    clock.value = 0.3
    await sink(" second")

    assert _deltas(events) == [(f"{ATTEMPT}.0", 1, "first second")]


@pytest.mark.asyncio
async def test_text_before_a_tool_call_or_a_new_pass_is_superseded() -> None:
    events = Events()
    streams = _streams(events, batch_chars=1)
    sink = streams.sink_for(TASK)
    assert sink is not None

    await sink("Let me check the source.")
    await sink.boundary()
    await sink("The answer.")
    await streams.begin_pass(TASK, 1)

    types = [draft.type for draft in events.drafts]
    assert types == ["message.delta", "attempt.superseded", "message.delta", "attempt.superseded"]
    first, second = (draft for draft in events.drafts if draft.type == "attempt.superseded")
    assert first.payload == {"supersededAttemptId": f"{ATTEMPT}.0", "replacementAttemptId": f"{ATTEMPT}.1"}
    assert second.payload == {"supersededAttemptId": f"{ATTEMPT}.1", "replacementAttemptId": f"{ATTEMPT}.2"}
    assert _deltas(events)[1] == (f"{ATTEMPT}.1", 1, "The answer.")


@pytest.mark.asyncio
async def test_a_boundary_with_nothing_shown_supersedes_nothing() -> None:
    events = Events()
    streams = _streams(events)
    sink = streams.sink_for(TASK)
    assert sink is not None

    await sink.boundary()
    await streams.begin_pass(TASK, 0)

    assert events.drafts == []


@pytest.mark.asyncio
async def test_a_long_flush_is_split_into_contract_sized_deltas() -> None:
    events = Events()
    streams = _streams(events, batch_chars=10 * MAX_DELTA_CHARS)
    sink = streams.sink_for(TASK)
    assert sink is not None

    await sink("x" * (MAX_DELTA_CHARS + 5))
    await sink.flush()

    assert [(sequence, len(text)) for _, sequence, text in _deltas(events)] == [(1, MAX_DELTA_CHARS), (2, 5)]


@pytest.mark.asyncio
async def test_event_store_failures_disable_the_live_view_without_failing_the_task() -> None:
    events = Events(failing=True)
    streams = _streams(events, batch_chars=1)
    sink = streams.sink_for(TASK)
    assert sink is not None

    for _ in range(MAX_PUBLISH_FAILURES + 3):
        await sink("token")

    assert sink.disabled
    assert events.attempts == MAX_PUBLISH_FAILURES


@pytest.mark.asyncio
async def test_text_that_cannot_be_withdrawn_stops_the_live_view_instead_of_mixing() -> None:
    events = Events()
    streams = _streams(events, batch_chars=1)
    sink = streams.sink_for(TASK)
    assert sink is not None
    await sink("Let me check.")

    events.failing = True
    await sink.boundary()
    events.failing = False
    await sink("The answer.")

    assert sink.disabled
    assert [draft.type for draft in events.drafts] == ["message.delta"]


@pytest.mark.asyncio
async def test_disabled_streaming_offers_no_sink_and_closing_publishes_nothing() -> None:
    events = Events()
    disabled = TaskStreams(events, enabled=False)
    disabled.open(TASK, session_id=SESSION, attempt_id=ATTEMPT)
    assert disabled.sink_for(TASK) is None

    streams = _streams(events, batch_chars=1_000)
    sink = streams.sink_for(TASK)
    assert sink is not None
    await sink("left over from a failed pass")
    await streams.close(TASK)

    assert streams.sink_for(TASK) is None
    assert events.drafts == []
