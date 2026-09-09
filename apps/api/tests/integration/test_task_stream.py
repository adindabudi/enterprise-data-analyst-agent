from __future__ import annotations

import fakeredis.aioredis
import pytest
from eda_runtime_state.events import EventDraft, RedisEventStore

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_stream_cursor_replay_preserves_event_identity() -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    store = RedisEventStore(redis, ttl_seconds=3600, max_entries=10000)
    first = await store.append(
        EventDraft(
            session_id="ses_1234567890abcdef",
            task_id="task_12345678",
            type="analysis_progress",
            payload={"milestone": "analysis", "detail": "first", "state": "running"},
        )
    )
    second = await store.append(
        EventDraft(
            session_id="ses_1234567890abcdef",
            task_id="task_12345678",
            type="analysis_progress",
            payload={"milestone": "analysis", "detail": "second", "state": "completed"},
        )
    )

    replay = await store.read_after("task_12345678", first.stream_id, block_ms=1)

    assert [entry.event.event_id for entry in replay] == [second.event.event_id]
    await redis.aclose()
