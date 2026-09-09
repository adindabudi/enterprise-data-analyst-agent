from __future__ import annotations

from collections.abc import Callable

import fakeredis.aioredis
import pytest
from eda_runtime_state.events import DegradedEventBuffer, EventDraft, RedisEventStore
from redis.crc import key_slot

TASK_ID = "task_12345678"


def test_event_body_omits_empty_provenance_array_for_redis_lua() -> None:
    body = EventDraft(
        session_id="ses_1234567890abcdef",
        task_id=TASK_ID,
        type="analysis_progress",
        payload={"milestone": "Agent is thinking", "state": "running"},
    ).body()

    assert "provenanceRefs" not in body


def test_event_stream_and_sequence_keys_share_cluster_slot() -> None:
    task_id = "task_12345678"

    stream_key = RedisEventStore._stream_key(task_id)
    sequence_key = RedisEventStore._sequence_key(task_id)

    assert key_slot(stream_key.encode()) == key_slot(sequence_key.encode())


@pytest.fixture
async def event_store() -> RedisEventStore:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield RedisEventStore(redis, ttl_seconds=3600, max_entries=10000)
    await redis.aclose()


@pytest.fixture
def draft_factory() -> Callable[[str], EventDraft]:
    def create(event_type: str) -> EventDraft:
        payloads: dict[str, dict[str, object]] = {
            "analysis_progress": {"milestone": "analysis", "detail": "running", "state": "running"},
            "task.checkpointed": {"status": "analyzing", "checkpointSequence": 1},
            "message.delta": {"delta": "partial response"},
            "stream.resumed": {"lastDurableSequence": 0},
        }
        return EventDraft(
            session_id="ses_1234567890abcdef",
            task_id=TASK_ID,
            type=event_type,
            payload=payloads[event_type],
        )

    return create


@pytest.mark.asyncio
async def test_append_assigns_task_global_sequences(
    event_store: RedisEventStore, draft_factory: Callable[[str], EventDraft]
) -> None:
    first = await event_store.append(draft_factory("analysis_progress"))
    second = await event_store.append(draft_factory("task.checkpointed"))

    assert first.event.sequence == 1
    assert second.event.sequence == 2
    assert first.stream_id != second.stream_id


@pytest.mark.asyncio
async def test_cursor_resumes_after_last_entry(
    event_store: RedisEventStore, draft_factory: Callable[[str], EventDraft]
) -> None:
    first = await event_store.append(draft_factory("analysis_progress"))
    second = await event_store.append(draft_factory("analysis_progress"))

    resumed = await event_store.read_after(TASK_ID, first.stream_id, block_ms=1)

    assert [entry.event.event_id for entry in resumed] == [second.event.event_id]


@pytest.mark.asyncio
async def test_recovery_reserves_explicit_omitted_interval(
    event_store: RedisEventStore, draft_factory: Callable[[str], EventDraft]
) -> None:
    await event_store.append(draft_factory("analysis_progress"))
    resumed = await event_store.append_resumed(draft_factory("stream.resumed"), dropped_count=3)

    assert resumed.event.sequence == 5
    assert resumed.event.payload.omitted_from_sequence == 2
    assert resumed.event.payload.omitted_to_sequence == 4


def test_degraded_buffer_never_exceeds_cap(draft_factory: Callable[[str], EventDraft]) -> None:
    buffer = DegradedEventBuffer(max_bytes=262144)
    for _ in range(10000):
        buffer.record(draft_factory("message.delta"))

    assert buffer.size_bytes <= 262144
    assert buffer.dropped_count == 10000
    assert buffer.latest_milestone is None
