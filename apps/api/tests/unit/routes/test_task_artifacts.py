# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from uuid import UUID

import pytest
from eda_api.auth.models import AuthSessionRecord, Principal
from eda_api.auth.repository import InMemoryAuthRepository
from eda_api.config import Settings
from eda_api.hosted_responses import HostedResponseAttempt, HostedResponseStatus
from eda_api.main import create_app
from eda_api.storage.artifacts import InMemoryArtifactCatalog, PublishedArtifact
from eda_api.storage.uploads import InMemoryBlobStore, InMemoryUploadRepository, UploadService
from eda_api.storage.workspace import InMemoryWorkspaceRepository
from eda_api.task_service import NullTaskEventStore
from eda_runtime_state.messages import InMemoryMessageRepository
from eda_runtime_state.tasks import InMemoryRuntimeStateRepository
from fastapi.testclient import TestClient

CSRF_TOKEN = "known-csrf"  # noqa: S105 - deterministic test-only CSRF fixture.


class FakeDurableClient:
    async def start(
        self,
        task_id: str,
        *,
        user_identity: str,
        previous_response_id: str | None = None,
    ) -> HostedResponseAttempt:
        del user_identity, previous_response_id
        return HostedResponseAttempt(id=f"resp_{task_id[5:]}", status=HostedResponseStatus.QUEUED)

    async def get(self, response_id: str, *, user_identity: str) -> HostedResponseAttempt:
        del user_identity
        return HostedResponseAttempt(id=response_id, status=HostedResponseStatus.IN_PROGRESS)

    async def cancel(self, response_id: str, *, user_identity: str) -> HostedResponseAttempt:
        del user_identity
        return HostedResponseAttempt(id=response_id, status=HostedResponseStatus.CANCELLED)

    async def close(self) -> None:
        return None


def _artifact(display_name: str = "analysis.xlsx", version: int = 2) -> PublishedArtifact:
    return PublishedArtifact(
        artifact_id="artifact-abcdefgh12345678",
        version=version,
        kind="xlsx",
        sha256="a" * 64,
        display_name=display_name,
        size_bytes=5,
    )


def _client(settings: Settings, catalog: InMemoryArtifactCatalog, owner_object_id: str) -> TestClient:
    owner = Principal(
        tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
        owner_object_id=UUID(owner_object_id),
        audience=UUID("33333333-3333-3333-3333-333333333333"),
    )
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
        workspace_repository_override=InMemoryWorkspaceRepository(),
        upload_service_override=UploadService(
            blob_store=InMemoryBlobStore(),
            upload_repository=InMemoryUploadRepository(),
            upload_limit_bytes=settings.upload_limit_bytes,
        ),
        runtime_repository_override=InMemoryRuntimeStateRepository(),
        event_store_override=NullTaskEventStore(),
        hosted_client_override=FakeDurableClient(),
        message_repository_override=InMemoryMessageRepository(),
        artifact_catalog_override=catalog,
    )
    client = TestClient(application, base_url="https://analyst.example.test", raise_server_exceptions=False)
    client.__enter__()
    client.cookies.set("eda_session", session.id)
    client.cookies.set("eda_csrf", CSRF_TOKEN)
    return client


@pytest.fixture
def catalog() -> InMemoryArtifactCatalog:
    return InMemoryArtifactCatalog()


@pytest.fixture
def owner(settings: Settings, catalog: InMemoryArtifactCatalog) -> Iterator[TestClient]:
    client = _client(settings, catalog, "22222222-2222-2222-2222-222222222222")
    try:
        yield client
    finally:
        client.__exit__(None, None, None)


@pytest.fixture
def other_owner(settings: Settings, catalog: InMemoryArtifactCatalog) -> Iterator[TestClient]:
    client = _client(settings, catalog, "44444444-4444-4444-4444-444444444444")
    try:
        yield client
    finally:
        client.__exit__(None, None, None)


def _headers(**extra: str) -> dict[str, str]:
    return {"Origin": "https://analyst.example.test", "X-CSRF-Token": CSRF_TOKEN, **extra}


def _create_task(client: TestClient) -> str:
    session_id = client.post("/api/sessions", json={"title": "Private analysis"}, headers=_headers()).json()[
        "sessionId"
    ]
    message_id = client.post(
        f"/api/sessions/{session_id}/messages",
        json={"text": "Build the workbook"},
        headers=_headers(**{"Idempotency-Key": "idem-message-0001"}),
    ).json()["messageId"]
    response = client.post(
        f"/api/sessions/{session_id}/tasks",
        json={"messageId": message_id},
        headers=_headers(**{"Idempotency-Key": "idem-task-0001"}),
    )
    assert response.status_code == 202
    return response.json()["taskId"]


def test_published_artifacts_are_listed_for_the_owning_task(
    owner: TestClient, catalog: InMemoryArtifactCatalog
) -> None:
    task_id = _create_task(owner)
    catalog.add(task_id, _artifact(), b"hello")

    response = owner.get(f"/api/tasks/{task_id}/artifacts")

    assert response.status_code == 200
    assert response.json() == {
        "artifacts": [
            {
                "artifactId": "artifact-abcdefgh12345678",
                "version": 2,
                "kind": "xlsx",
                "sha256": "a" * 64,
                "displayName": "analysis.xlsx",
                "sizeBytes": 5,
            }
        ]
    }


def test_artifact_content_downloads_as_an_inert_attachment(owner: TestClient, catalog: InMemoryArtifactCatalog) -> None:
    task_id = _create_task(owner)
    artifact = _artifact()
    catalog.add(task_id, artifact, b"hello")

    response = owner.get(f"/api/tasks/{task_id}/artifacts/{artifact.artifact_id}/versions/{artifact.version}/content")

    assert response.status_code == 200
    assert response.content == b"hello"
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["content-disposition"] == 'attachment; filename="analysis.xlsx"'
    assert response.headers["x-content-type-options"] == "nosniff"


def test_a_traversal_display_name_cannot_escape_the_download_filename(
    owner: TestClient, catalog: InMemoryArtifactCatalog
) -> None:
    task_id = _create_task(owner)
    artifact = _artifact(display_name="../../etc/passwd")
    catalog.add(task_id, artifact, b"hello")

    response = owner.get(f"/api/tasks/{task_id}/artifacts/{artifact.artifact_id}/versions/{artifact.version}/content")

    assert response.headers["content-disposition"] == 'attachment; filename="_.._etc_passwd"'


def test_another_owner_cannot_list_or_download_the_artifacts(
    owner: TestClient, other_owner: TestClient, catalog: InMemoryArtifactCatalog
) -> None:
    task_id = _create_task(owner)
    artifact = _artifact()
    catalog.add(task_id, artifact, b"hello")

    assert other_owner.get(f"/api/tasks/{task_id}/artifacts").status_code == 404
    assert (
        other_owner.get(
            f"/api/tasks/{task_id}/artifacts/{artifact.artifact_id}/versions/{artifact.version}/content"
        ).status_code
        == 404
    )


def test_an_unpublished_version_is_not_downloadable(owner: TestClient, catalog: InMemoryArtifactCatalog) -> None:
    task_id = _create_task(owner)
    artifact = _artifact()
    catalog.add(task_id, artifact, b"hello")

    response = owner.get(f"/api/tasks/{task_id}/artifacts/{artifact.artifact_id}/versions/1/content")

    assert response.status_code == 404
