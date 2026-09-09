from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from secrets import token_urlsafe
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from eda_api.auth.models import AuthSessionRecord, Principal
from eda_api.auth.repository import InMemoryAuthRepository
from eda_api.config import Settings
from eda_api.main import create_app
from eda_api.storage.history import SessionHistory, SessionMessageView
from eda_api.storage.uploads import InMemoryBlobStore, InMemoryUploadRepository, UploadService
from eda_api.storage.workspace import InMemoryWorkspaceRepository
from eda_api.task_service import NullTaskEventStore
from eda_runtime_state.messages import InMemoryMessageRepository
from eda_runtime_state.tasks import InMemoryRuntimeStateRepository
from fastapi.testclient import TestClient


@pytest.mark.parametrize("owns_session", [True, False])
def test_session_history_is_restorable_only_by_its_owner(settings: Settings, owns_session: bool) -> None:
    owner = Principal(tenant_id=UUID(int=1), owner_object_id=UUID(int=2), audience=UUID(int=3))
    principal = owner if owns_session else owner.model_copy(update={"owner_object_id": UUID(int=4)})
    workspace = InMemoryWorkspaceRepository()
    session = asyncio.run(workspace.create_session(owner, "Room occupancy"))
    auth = InMemoryAuthRepository()
    login = AuthSessionRecord.create(principal, csrf_token=token_urlsafe(32), ttl_seconds=300, id="auth_history_12345678")
    asyncio.run(auth.put_session(login))
    history = SessionHistory(messages=(SessionMessageView(
        message_id="msg_history_12345678", role="user", text="Export results", created_at=datetime.now(UTC),
    ),))
    reader = SimpleNamespace(read_history=AsyncMock(return_value=history))

    for _ in range(2):
        application = create_app(
            settings_override=settings, auth_repository_override=auth, workspace_repository_override=workspace,
            upload_service_override=UploadService(
                blob_store=InMemoryBlobStore(), upload_repository=InMemoryUploadRepository(),
                upload_limit_bytes=settings.upload_limit_bytes,
            ),
            runtime_repository_override=InMemoryRuntimeStateRepository(), event_store_override=NullTaskEventStore(),
            message_repository_override=InMemoryMessageRepository(), session_history_reader_override=reader,
        )
        with TestClient(application, base_url="https://analyst.example.test") as client:
            client.cookies.set("eda_session", login.id)
            response = client.get(f"/api/sessions/{session.session_id}/history")
        assert response.status_code == (200 if owns_session else 404)
        if owns_session:
            assert response.json() == history.model_dump(mode="json", by_alias=True)
    assert reader.read_history.await_count == (2 if owns_session else 0)