from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

import fakeredis.aioredis
import pytest
from eda_contracts.controls import CommandKind
from eda_contracts.tasks import TaskStatus
from eda_runtime_state.events import EventDraft, RedisEventStore
from eda_runtime_state.models import TaskRecord
from eda_runtime_state.tasks import InMemoryRuntimeStateRepository

pytestmark = pytest.mark.integration


class FakeAgentResult(Protocol):
    applied_command_sequence: int


class FakeAgent(Protocol):
    async def run(self, *, pending_command_ids: tuple[str, ...] = ()) -> FakeAgentResult: ...


async def test_fake_agent_consumes_fifo_command_ids_and_streams_typed_events(
    deterministic_fake_agent: FakeAgent,
) -> None:
    now = datetime.now(UTC)
    task = TaskRecord(
        id="task_12345678",
        tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
        owner_object_id=UUID("22222222-2222-2222-2222-222222222222"),
        session_id="ses_1234567890abcdef",
        status=TaskStatus.ANALYZING,
        checkpoint_sequence=1,
        command_sequence=0,
        applied_command_sequence=0,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(days=1),
    )
    repository = InMemoryRuntimeStateRepository()
    await repository.create_task(task, "request-12345678")
    first = await repository.append_command(task.partition(), task.id, CommandKind.STEER, "check APAC", "command-1")
    second = await repository.append_command(task.partition(), task.id, CommandKind.CANCEL, None, "command-2")
    result = await deterministic_fake_agent.run(
        pending_command_ids=tuple(command.id for command in await repository.pending_commands(task.id))
    )
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    events = RedisEventStore(redis, ttl_seconds=3600, max_entries=10000)
    await events.append(
        EventDraft(
            session_id=task.session_id,
            task_id=task.id,
            type="analysis_progress",
            payload={"milestone": "analyzing", "detail": "bounded", "state": "running"},
        )
    )

    assert (first.sequence, second.sequence) == (1, 2)
    assert result.applied_command_sequence == 2
    assert [entry.event.sequence for entry in await events.read_after(task.id, "0-0", block_ms=1)] == [1]
    await redis.aclose()
