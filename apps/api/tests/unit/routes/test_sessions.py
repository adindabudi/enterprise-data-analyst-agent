from __future__ import annotations

import asyncio
from dataclasses import dataclass
from uuid import UUID

import pytest
from eda_api.auth.models import AuthSessionRecord, Principal
from eda_api.auth.repository import InMemoryAuthRepository
from eda_api.config import Settings
from eda_api.main import create_app
from eda_api.storage.uploads import InMemoryBlobStore, InMemoryUploadRepository, UploadService
from eda_api.storage.workspace import InMemoryWorkspaceRepository
from eda_api.task_service import NullTaskEventStore
from eda_runtime_state.tasks import InMemoryRuntimeStateRepository
from fastapi.testclient import TestClient


@dataclass
class AuthenticatedClient:
    client: TestClient
    csrf_headers: dict[str, str]


def principal(owner_object_id: str) -> Principal:
    return Principal(
        tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
        owner_object_id=UUID(owner_object_id),
        audience=UUID("33333333-3333-3333-3333-333333333333"),
    )


def authenticated_client(
    settings: Settings,
    workspace_repository: InMemoryWorkspaceRepository,
    msal_client: object,
    owner: Principal,
) -> AuthenticatedClient:
    auth_repository = InMemoryAuthRepository()
    csrf_token = "-".join(("known", "csrf"))
    session = AuthSessionRecord.create(
        owner,
        csrf_token=csrf_token,
        ttl_seconds=300,
        id=f"auth_{owner.owner_object_id.hex}_session",
    )
    asyncio.run(auth_repository.put_session(session))
    application = create_app(
        settings_override=settings,
        auth_repository_override=auth_repository,
        msal_override=msal_client,
        upload_service_override=UploadService(
            blob_store=InMemoryBlobStore(),
            upload_repository=InMemoryUploadRepository(),
            upload_limit_bytes=settings.upload_limit_bytes,
        ),
        runtime_repository_override=InMemoryRuntimeStateRepository(),
        event_store_override=NullTaskEventStore(),
    )
    application.state.workspace_repository_override = workspace_repository
    test_client = TestClient(application, base_url="https://analyst.example.test", raise_server_exceptions=False)
    test_client.__enter__()
    test_client.cookies.set("eda_session", session.id)
    test_client.cookies.set("eda_csrf", csrf_token)
    return AuthenticatedClient(
        client=test_client,
        csrf_headers={"Origin": "https://analyst.example.test", "X-CSRF-Token": csrf_token},
    )


@pytest.fixture
def workspace_repository() -> InMemoryWorkspaceRepository:
    return InMemoryWorkspaceRepository()


@pytest.fixture
def owner_client(
    settings: Settings, workspace_repository: InMemoryWorkspaceRepository, msal_client: object
) -> AuthenticatedClient:
    test_client = authenticated_client(
        settings,
        workspace_repository,
        msal_client,
        principal("22222222-2222-2222-2222-222222222222"),
    )
    try:
        yield test_client
    finally:
        test_client.client.__exit__(None, None, None)


@pytest.fixture
def other_owner_client(
    settings: Settings, workspace_repository: InMemoryWorkspaceRepository, msal_client: object
) -> AuthenticatedClient:
    test_client = authenticated_client(
        settings,
        workspace_repository,
        msal_client,
        principal("44444444-4444-4444-4444-444444444444"),
    )
    try:
        yield test_client
    finally:
        test_client.client.__exit__(None, None, None)


def test_create_rejects_client_owner_fields(owner_client: AuthenticatedClient) -> None:
    response = owner_client.client.post(
        "/api/sessions",
        json={
            "title": "Revenue review",
            "tenantId": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "ownerObjectId": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        },
        headers=owner_client.csrf_headers,
    )

    assert response.status_code == 422


def test_other_owner_gets_not_found_not_forbidden(
    owner_client: AuthenticatedClient, other_owner_client: AuthenticatedClient
) -> None:
    created = owner_client.client.post("/api/sessions", json={"title": "Private"}, headers=owner_client.csrf_headers)

    assert created.status_code == 201
    session_id = created.json()["sessionId"]
    response = other_owner_client.client.get(f"/api/sessions/{session_id}")

    assert response.status_code == 404


def test_rename_requires_current_etag(owner_client: AuthenticatedClient) -> None:
    created = owner_client.client.post("/api/sessions", json={"title": "First"}, headers=owner_client.csrf_headers)

    assert created.status_code == 201
    response = owner_client.client.patch(
        f"/api/sessions/{created.json()['sessionId']}",
        json={"title": "Second"},
        headers={**owner_client.csrf_headers, "If-Match": '"stale"'},
    )

    assert response.status_code == 409


def test_session_responses_exclude_partition_fields(owner_client: AuthenticatedClient) -> None:
    created = owner_client.client.post("/api/sessions", json={"title": "Private"}, headers=owner_client.csrf_headers)

    assert created.status_code == 201
    assert {"tenantId", "ownerObjectId", "_etag"}.isdisjoint(created.json())
    assert "ETag" in created.headers


def test_list_returns_only_the_authenticated_owner_sessions(
    owner_client: AuthenticatedClient, other_owner_client: AuthenticatedClient
) -> None:
    owner_client.client.post("/api/sessions", json={"title": "Owner"}, headers=owner_client.csrf_headers)
    other_owner_client.client.post("/api/sessions", json={"title": "Other"}, headers=other_owner_client.csrf_headers)

    response = owner_client.client.get("/api/sessions")

    assert response.status_code == 200
    assert [session["title"] for session in response.json()] == ["Owner"]


def test_delete_records_intent_without_removing_session(owner_client: AuthenticatedClient) -> None:
    created = owner_client.client.post(
        "/api/sessions", json={"title": "Deferred cleanup"}, headers=owner_client.csrf_headers
    )
    session_id = created.json()["sessionId"]

    response = owner_client.client.delete(f"/api/sessions/{session_id}", headers=owner_client.csrf_headers)

    assert response.status_code == 202
    assert owner_client.client.get(f"/api/sessions/{session_id}").status_code == 200
