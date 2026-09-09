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
from eda_contracts import ArtifactKind
from eda_runtime_state.messages import InMemoryMessageRepository
from eda_runtime_state.models import QueryResultRef
from eda_runtime_state.tasks import InMemoryRuntimeStateRepository
from fastapi.testclient import TestClient

CSRF_TOKEN = "known-csrf"  # noqa: S105 - deterministic test-only CSRF fixture.
QUERY = "MATCH (p:patients)-[:patients_has_rooms]->(r:rooms) RETURN p.PatientId AS id"


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


@pytest.fixture
def catalog() -> InMemoryArtifactCatalog:
    return InMemoryArtifactCatalog()


@pytest.fixture
def runtime() -> InMemoryRuntimeStateRepository:
    return InMemoryRuntimeStateRepository()


@pytest.fixture
def owner(
    settings: Settings, catalog: InMemoryArtifactCatalog, runtime: InMemoryRuntimeStateRepository
) -> Iterator[TestClient]:
    principal = Principal(
        tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
        owner_object_id=UUID("22222222-2222-2222-2222-222222222222"),
        audience=UUID("33333333-3333-3333-3333-333333333333"),
    )
    auth_repository = InMemoryAuthRepository()
    session = AuthSessionRecord.create(
        principal, csrf_token=CSRF_TOKEN, ttl_seconds=300, id=f"auth_{principal.owner_object_id.hex}_session"
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
        runtime_repository_override=runtime,
        event_store_override=NullTaskEventStore(),
        hosted_client_override=FakeDurableClient(),
        message_repository_override=InMemoryMessageRepository(),
        artifact_catalog_override=catalog,
    )
    client = TestClient(application, base_url="https://analyst.example.test", raise_server_exceptions=False)
    client.__enter__()
    client.cookies.set("eda_session", session.id)
    client.cookies.set("eda_csrf", CSRF_TOKEN)
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
        json={"text": "Export the ICU patients"},
        headers=_headers(**{"Idempotency-Key": "idem-message-0001"}),
    ).json()["messageId"]
    return str(
        client.post(
            f"/api/sessions/{session_id}/tasks",
            json={"messageId": message_id},
            headers=_headers(**{"Idempotency-Key": "idem-task-0001"}),
        ).json()["taskId"]
    )


async def _attach_query(runtime: InMemoryRuntimeStateRepository, task_id: str) -> None:
    task = await runtime.resolve_task(task_id)
    assert task is not None
    stored = QueryResultRef(
        artifact_id="artifact-0123456789abcdef0123456789abcdef01234567",
        version=1,
        kind="data",
        sha256="b" * 64,
        display_name="query-result-1.json",
        query=QUERY,
        query_sha256="c" * 64,
        row_count=44,
        source_alias="lamna",
        executed_at=task.created_at,
    )
    runtime._tasks[task_id] = task.model_copy(update={"query_results": (stored,)})


def test_provenance_names_what_the_analysis_read_and_what_it_published(
    owner: TestClient, catalog: InMemoryArtifactCatalog, runtime: InMemoryRuntimeStateRepository
) -> None:
    task_id = _create_task(owner)
    asyncio.run(_attach_query(runtime, task_id))
    catalog.add(
        task_id,
        PublishedArtifact(
            artifact_id="artifact-abcdefgh12345678",
            version=2,
            kind=ArtifactKind.XLSX,
            sha256="a" * 64,
            display_name="analysis.xlsx",
            size_bytes=5,
        ),
        b"hello",
    )

    body = owner.get(f"/api/tasks/{task_id}/provenance").json()

    # A published file on its own proves nothing about where its numbers came from.
    assert [entry["query"] for entry in body["sourceQueries"]] == [QUERY]
    assert body["sourceQueries"][0]["rowCount"] == 44
    assert body["sourceQueries"][0]["sourceAlias"] == "lamna"
    assert body["sourceQueries"][0]["querySha256"] == "c" * 64
    assert [entry["displayName"] for entry in body["artifacts"]] == ["analysis.xlsx"]


def test_provenance_is_empty_rather_than_absent_for_a_task_that_read_nothing(owner: TestClient) -> None:
    task_id = _create_task(owner)

    response = owner.get(f"/api/tasks/{task_id}/provenance")

    assert response.status_code == 200
    assert response.json() == {"sourceQueries": [], "artifacts": []}


def test_provenance_of_another_owner_task_is_not_readable(
    owner: TestClient, runtime: InMemoryRuntimeStateRepository
) -> None:
    task_id = _create_task(owner)
    asyncio.run(_attach_query(runtime, task_id))

    response = owner.get("/api/tasks/task_notmine12345678/provenance")

    assert response.status_code == 404
