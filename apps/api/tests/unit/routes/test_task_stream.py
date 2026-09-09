from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from eda_api.auth.models import AuthSessionRecord, Principal
from eda_api.auth.repository import InMemoryAuthRepository
from eda_api.config import Settings
from eda_api.main import create_app
from eda_api.routes.tasks import stream_task_events
from eda_api.storage.uploads import InMemoryBlobStore, InMemoryUploadRepository, UploadService
from eda_api.storage.workspace import InMemoryWorkspaceRepository
from eda_api.task_service import TaskService
from eda_contracts.tasks import TaskStatus
from eda_runtime_state.events import EventDraft
from eda_runtime_state.models import TaskRecord
from eda_runtime_state.tasks import InMemoryRuntimeStateRepository
from fastapi.testclient import TestClient


class EmptyEventStore:
    async def exists(self, task_id: str) -> bool:
        del task_id
        return False


class DelayedEventStore:
    def __init__(self, entries: list[object]) -> None:
        self.entries = entries
        self.read_cursors: list[str] = []

    async def exists(self, task_id: str) -> bool:
        del task_id
        return False

    async def read_after(self, task_id: str, cursor: str, *, block_ms: int = 15000) -> list[object]:
        del task_id, block_ms
        self.read_cursors.append(cursor)
        entries, self.entries = self.entries, []
        return entries


class ConnectedRequest:
    async def is_disconnected(self) -> bool:
        return False


def test_other_owner_cannot_open_stream(settings: Settings, msal_client: object) -> None:
    now = datetime.now(UTC)
    owner = Principal(
        tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
        owner_object_id=UUID("22222222-2222-2222-2222-222222222222"),
        audience=UUID("33333333-3333-3333-3333-333333333333"),
    )
    other = owner.model_copy(update={"owner_object_id": UUID("44444444-4444-4444-4444-444444444444")})
    task = TaskRecord(
        id="task_12345678",
        tenant_id=owner.tenant_id,
        owner_object_id=owner.owner_object_id,
        session_id="ses_1234567890abcdef",
        status=TaskStatus.COMPLETED,
        checkpoint_sequence=1,
        command_sequence=0,
        applied_command_sequence=0,
        final_message_id="msg_12345678",
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(days=1),
    )
    repository = InMemoryRuntimeStateRepository()
    asyncio.run(repository.create_task(task, "request-12345678"))
    auth_repository = InMemoryAuthRepository()
    csrf_token = "-".join(("known", "csrf"))
    auth_session = AuthSessionRecord.create(other, csrf_token=csrf_token, ttl_seconds=300, id="auth_stream_1234567890")
    asyncio.run(auth_repository.put_session(auth_session))
    application = create_app(
        settings_override=settings,
        auth_repository_override=auth_repository,
        msal_override=msal_client,
        workspace_repository_override=InMemoryWorkspaceRepository(),
        upload_service_override=UploadService(
            blob_store=InMemoryBlobStore(),
            upload_repository=InMemoryUploadRepository(),
            upload_limit_bytes=settings.upload_limit_bytes,
        ),
        runtime_repository_override=repository,
        event_store_override=EmptyEventStore(),
    )
    with TestClient(application, base_url="https://analyst.example.test", raise_server_exceptions=True) as client:
        response = client.get(f"/api/tasks/{task.id}/events", cookies={"eda_session": auth_session.id})

    assert response.status_code == 404


def test_terminal_task_stream_uses_canonical_snapshot(settings: Settings, msal_client: object) -> None:
    now = datetime.now(UTC)
    owner = Principal(
        tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
        owner_object_id=UUID("22222222-2222-2222-2222-222222222222"),
        audience=UUID("33333333-3333-3333-3333-333333333333"),
    )
    task = TaskRecord(
        id="task_snapshot_12345678",
        tenant_id=owner.tenant_id,
        owner_object_id=owner.owner_object_id,
        session_id="ses_1234567890abcdef",
        status=TaskStatus.COMPLETED,
        checkpoint_sequence=7,
        command_sequence=0,
        applied_command_sequence=0,
        final_message_id="msg_12345678",
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(days=1),
    )
    repository = InMemoryRuntimeStateRepository()
    asyncio.run(repository.create_task(task, "request-12345678"))
    auth_repository = InMemoryAuthRepository()
    csrf_token = "-".join(("known", "csrf"))
    auth_session = AuthSessionRecord.create(
        owner, csrf_token=csrf_token, ttl_seconds=300, id="auth_snapshot_1234567890"
    )
    asyncio.run(auth_repository.put_session(auth_session))
    application = create_app(
        settings_override=settings,
        auth_repository_override=auth_repository,
        msal_override=msal_client,
        workspace_repository_override=InMemoryWorkspaceRepository(),
        upload_service_override=UploadService(
            blob_store=InMemoryBlobStore(),
            upload_repository=InMemoryUploadRepository(),
            upload_limit_bytes=settings.upload_limit_bytes,
        ),
        runtime_repository_override=repository,
        event_store_override=EmptyEventStore(),
    )
    with TestClient(application, base_url="https://analyst.example.test", raise_server_exceptions=True) as client:
        response = client.get(f"/api/tasks/{task.id}/events", cookies={"eda_session": auth_session.id})

    assert response.status_code == 200
    assert "event: task.checkpointed" in response.text
    assert "event: run.completed" in response.text
    assert response.text.count(task.final_message_id) == 1


@pytest.mark.asyncio
class UnknownOrchestrations:
    async def get_orchestration_state(self, instance_id: str, *, fetch_payloads: bool = True) -> None:
        del instance_id, fetch_payloads
        return None


async def test_nonterminal_snapshot_waits_for_live_terminal_event() -> None:
    now = datetime.now(UTC)
    task = TaskRecord(
        id="task_stream_12345678",
        tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
        owner_object_id=UUID("22222222-2222-2222-2222-222222222222"),
        session_id="ses_1234567890abcdef",
        status=TaskStatus.PLANNING,
        checkpoint_sequence=0,
        command_sequence=0,
        applied_command_sequence=0,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(days=1),
    )
    analyzing = TaskService._entry(
        EventDraft(
            session_id=task.session_id,
            task_id=task.id,
            type="analysis_progress",
            payload={"milestone": "Agent is thinking", "state": "running"},
        ),
        stream_id="1-0",
    )
    completed = TaskService._entry(
        EventDraft(
            session_id=task.session_id,
            task_id=task.id,
            type="run.completed",
            payload={"status": "completed", "finalMessageId": "msg_12345678"},
        ),
        stream_id="2-0",
    )
    events = DelayedEventStore([analyzing, completed])
    service = TaskService(None, None, UnknownOrchestrations(), events)  # type: ignore[arg-type]

    streamed = [
        event
        async for event in stream_task_events(
            ConnectedRequest(),  # type: ignore[arg-type]
            task,
            service,
            None,
        )
    ]

    assert [event.event for event in streamed] == [
        "task.checkpointed",
        "analysis_progress",
        "run.completed",
    ]
    assert streamed[0].id is None
    assert events.read_cursors == ["0-0"]
