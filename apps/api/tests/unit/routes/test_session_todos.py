# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from uuid import UUID

import pytest
from eda_api.auth.models import AuthSessionRecord, Principal
from eda_api.auth.repository import InMemoryAuthRepository
from eda_api.config import Settings
from eda_api.main import create_app
from eda_api.storage.todos import InMemorySessionTodoReader, TodoItemView
from eda_api.storage.uploads import InMemoryBlobStore, InMemoryUploadRepository, UploadService
from eda_api.storage.workspace import InMemoryWorkspaceRepository
from eda_api.task_service import NullTaskEventStore
from eda_runtime_state.messages import InMemoryMessageRepository
from eda_runtime_state.tasks import InMemoryRuntimeStateRepository
from fastapi.testclient import TestClient

CSRF_TOKEN = "known-csrf"  # noqa: S105 - deterministic test-only CSRF fixture.
OWNER = UUID("22222222-2222-2222-2222-222222222222")
OTHER_OWNER = UUID("44444444-4444-4444-4444-444444444444")


def _principal(owner_object_id: UUID) -> Principal:
    return Principal(
        tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
        owner_object_id=owner_object_id,
        audience=UUID("33333333-3333-3333-3333-333333333333"),
    )


def _client(
    settings: Settings,
    workspace: InMemoryWorkspaceRepository,
    reader: InMemorySessionTodoReader,
    owner_object_id: UUID,
) -> TestClient:
    owner = _principal(owner_object_id)
    auth_repository = InMemoryAuthRepository()
    session = AuthSessionRecord.create(
        owner,
        csrf_token=CSRF_TOKEN,
        ttl_seconds=300,
        id=f"auth_{owner.owner_object_id.hex}_session",
    )
    asyncio.run(auth_repository.put_session(session))
    application = create_app(
        settings_override=settings,
        auth_repository_override=auth_repository,
        workspace_repository_override=workspace,
        upload_service_override=UploadService(
            blob_store=InMemoryBlobStore(),
            upload_repository=InMemoryUploadRepository(),
            upload_limit_bytes=settings.upload_limit_bytes,
        ),
        runtime_repository_override=InMemoryRuntimeStateRepository(),
        event_store_override=NullTaskEventStore(),
        message_repository_override=InMemoryMessageRepository(),
        session_todo_reader_override=reader,
    )
    client = TestClient(application, base_url="https://analyst.example.test", raise_server_exceptions=False)
    client.__enter__()
    client.cookies.set("eda_session", session.id)
    client.cookies.set("eda_csrf", CSRF_TOKEN)
    return client


@pytest.fixture
def workspace() -> InMemoryWorkspaceRepository:
    return InMemoryWorkspaceRepository()


@pytest.fixture
def reader() -> InMemorySessionTodoReader:
    return InMemorySessionTodoReader()


@pytest.fixture
def owner(
    settings: Settings, workspace: InMemoryWorkspaceRepository, reader: InMemorySessionTodoReader
) -> Iterator[TestClient]:
    client = _client(settings, workspace, reader, OWNER)
    try:
        yield client
    finally:
        client.__exit__(None, None, None)


@pytest.fixture
def other_owner(
    settings: Settings, workspace: InMemoryWorkspaceRepository, reader: InMemorySessionTodoReader
) -> Iterator[TestClient]:
    client = _client(settings, workspace, reader, OTHER_OWNER)
    try:
        yield client
    finally:
        client.__exit__(None, None, None)


def _headers() -> dict[str, str]:
    return {"Origin": "https://analyst.example.test", "X-CSRF-Token": CSRF_TOKEN}


def _create_session(client: TestClient) -> str:
    response = client.post("/api/sessions", json={"title": "Private analysis"}, headers=_headers())
    assert response.status_code == 201
    return response.json()["sessionId"]


def test_the_agent_todo_list_is_returned_for_the_owning_session(
    owner: TestClient, reader: InMemorySessionTodoReader
) -> None:
    session_id = _create_session(owner)
    reader.set(
        _principal(OWNER),
        session_id,
        (
            TodoItemView(id=1, title="Reconcile the control total", is_complete=True),
            TodoItemView(id=2, title="Publish the workbook", description="After validation passes"),
        ),
    )

    response = owner.get(f"/api/sessions/{session_id}/todos")

    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {"id": 1, "title": "Reconcile the control total", "description": None, "isComplete": True},
            {
                "id": 2,
                "title": "Publish the workbook",
                "description": "After validation passes",
                "isComplete": False,
            },
        ]
    }


def test_a_session_without_agent_todos_returns_an_empty_list(owner: TestClient) -> None:
    session_id = _create_session(owner)

    response = owner.get(f"/api/sessions/{session_id}/todos")

    assert response.status_code == 200
    assert response.json() == {"items": []}


def test_another_owner_cannot_read_the_todo_list(
    owner: TestClient, other_owner: TestClient, reader: InMemorySessionTodoReader
) -> None:
    session_id = _create_session(owner)
    reader.set(_principal(OWNER), session_id, (TodoItemView(id=1, title="Reconcile the control total"),))

    assert other_owner.get(f"/api/sessions/{session_id}/todos").status_code == 404
