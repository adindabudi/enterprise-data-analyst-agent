from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from eda_api.task_service import TaskService
from eda_contracts.controls import CommandKind
from eda_contracts.tasks import TaskStatus
from eda_runtime_state.messages import InMemoryMessageRepository
from eda_runtime_state.models import TaskRecord
from eda_runtime_state.tasks import InMemoryRuntimeStateRepository

pytestmark = pytest.mark.integration


class _FakeHostedClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def close(self) -> object:
        return None


@pytest.mark.asyncio
async def test_resume_auth_persists_for_the_next_phase_boundary_and_is_idempotent() -> None:
    now = datetime.now(UTC)
    task = TaskRecord(
        id="task_resume1234",
        tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
        owner_object_id=UUID("22222222-2222-2222-2222-222222222222"),
        session_id="ses_1234567890abcdef",
        status=TaskStatus.BLOCKED_AUTH,
        checkpoint_sequence=4,
        command_sequence=0,
        applied_command_sequence=0,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(days=1),
    )
    repository = InMemoryRuntimeStateRepository()
    await repository.create_task(task, "request-resume-auth")
    hosted = _FakeHostedClient()
    service = TaskService(repository, InMemoryMessageRepository(), hosted)  # type: ignore[arg-type]

    first = await service.resume_auth(task.partition(), task.id, "receipt-abc123")
    second = await service.resume_auth(task.partition(), task.id, "receipt-abc123")
    pending = await repository.pending_commands(task.id)

    assert first.id == second.id
    assert first.kind is CommandKind.AUTH_RESUMED
    assert [command.kind for command in pending] == [CommandKind.AUTH_RESUMED]
    assert hosted.calls == []
