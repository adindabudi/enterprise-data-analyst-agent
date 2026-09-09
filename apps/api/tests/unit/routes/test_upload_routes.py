from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Iterator
from uuid import UUID

import pytest
from azure.core.exceptions import ResourceNotFoundError
from eda_api.auth.models import AuthSessionRecord, Principal
from eda_api.auth.repository import InMemoryAuthRepository
from eda_api.config import Settings
from eda_api.main import create_app
from eda_api.storage.scans import SCAN_RESULT_TAG
from eda_api.storage.uploads import InMemoryBlobStore, InMemoryUploadRepository, UploadService
from eda_api.storage.workspace import InMemoryWorkspaceRepository
from eda_api.task_service import NullTaskEventStore
from eda_runtime_state.tasks import InMemoryRuntimeStateRepository
from fastapi.testclient import TestClient


def test_upload_returns_scanning_metadata_for_owner_session(settings: Settings, msal_client: object) -> None:
    owner = Principal(
        tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
        owner_object_id=UUID("22222222-2222-2222-2222-222222222222"),
        audience=UUID("33333333-3333-3333-3333-333333333333"),
    )
    auth_repository = InMemoryAuthRepository()
    workspace_repository = InMemoryWorkspaceRepository()
    upload_service = UploadService(
        blob_store=InMemoryBlobStore(),
        upload_repository=InMemoryUploadRepository(),
        upload_limit_bytes=settings.upload_limit_bytes,
    )
    csrf_token = "-".join(("known", "csrf"))
    auth_session = AuthSessionRecord.create(
        owner,
        csrf_token=csrf_token,
        ttl_seconds=300,
        id="auth_upload_route_1234567890",
    )
    asyncio.run(auth_repository.put_session(auth_session))
    session = asyncio.run(workspace_repository.create_session(owner, "Uploads"))
    application = create_app(
        settings_override=settings,
        auth_repository_override=auth_repository,
        msal_override=msal_client,
        workspace_repository_override=workspace_repository,
        upload_service_override=upload_service,
        runtime_repository_override=InMemoryRuntimeStateRepository(),
        event_store_override=NullTaskEventStore(),
    )
    with TestClient(application, base_url="https://analyst.example.test") as client:
        response = client.post(
            f"/api/sessions/{session.session_id}/uploads",
            files={"upload": ("browser-name.csv", b"name,value\nrevenue,42\n", "text/csv")},
            cookies={"eda_session": auth_session.id, "eda_csrf": csrf_token},
            headers={"Origin": "https://analyst.example.test", "X-CSRF-Token": csrf_token},
        )

    assert response.status_code == 202
    assert response.json()["state"] == "scanning"
    assert set(response.json()) == {"uploadId", "displayName", "state"}


class TaggedBlobStore(InMemoryBlobStore):
    def __init__(self) -> None:
        super().__init__()
        self.tags: dict[str, str] = {}
        self.tag_reads: list[str] = []

    async def get_tags(self, name: str) -> dict[str, str]:
        self.tag_reads.append(name)
        return self.tags.copy()


@pytest.fixture
def status_client(settings: Settings, msal_client: object) -> Iterator[tuple[TestClient, str, TaggedBlobStore]]:
    owner = Principal(
        tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
        owner_object_id=UUID("22222222-2222-2222-2222-222222222222"),
        audience=UUID("33333333-3333-3333-3333-333333333333"),
    )
    repository = InMemoryAuthRepository()
    workspace = InMemoryWorkspaceRepository()
    blobs = TaggedBlobStore()
    service = UploadService(
        blob_store=blobs,
        upload_repository=InMemoryUploadRepository(),
        upload_limit_bytes=settings.upload_limit_bytes,
    )
    csrf_token = "-".join(("known", "csrf"))
    for label, principal in (
        ("owner", owner),
        ("other-owner", owner.model_copy(update={"owner_object_id": UUID(int=4)})),
        ("other-tenant", owner.model_copy(update={"tenant_id": UUID(int=5)})),
    ):
        auth = AuthSessionRecord.create(
            principal, csrf_token=csrf_token, ttl_seconds=300, id=f"auth_upload_{label}_1234567890"
        )
        asyncio.run(repository.put_session(auth))
    session = asyncio.run(workspace.create_session(owner, "Upload status"))
    application = create_app(
        settings_override=settings,
        auth_repository_override=repository,
        msal_override=msal_client,
        workspace_repository_override=workspace,
        upload_service_override=service,
        runtime_repository_override=InMemoryRuntimeStateRepository(),
        event_store_override=NullTaskEventStore(),
    )
    with TestClient(application, base_url="https://analyst.example.test") as client:
        client.cookies.set("eda_session", "auth_upload_owner_1234567890")
        client.cookies.set("eda_csrf", "known-csrf")
        yield client, session.session_id, blobs


@pytest.mark.parametrize(
    ("tag", "expected_state"),
    [
        (None, "scanning"),
        ("No threats found", "clean"),
        ("Malicious", "rejected"),
        ("Error", "scan_failed"),
        ("Not scanned", "scan_failed"),
        ("unexpected", "scanning"),
    ],
)
def test_upload_status_uses_authoritative_scan_tags(
    status_client: tuple[TestClient, str, TaggedBlobStore], tag: str | None, expected_state: str
) -> None:
    client, session_id, blobs = status_client
    content = b"name,value\nrevenue,42\n"
    uploaded = client.post(
        f"/api/sessions/{session_id}/uploads",
        files={"upload": ("data.csv", content, "text/csv")},
        headers={"Origin": "https://analyst.example.test", "X-CSRF-Token": "known-csrf"},
    )
    assert uploaded.status_code == 202
    upload_id = uploaded.json()["uploadId"]
    if tag is not None:
        blobs.tags[SCAN_RESULT_TAG] = tag

    response = client.get(f"/api/sessions/{session_id}/uploads/{upload_id}")

    assert response.status_code == 200
    assert response.json() == {
        "uploadId": upload_id,
        "displayName": "data.csv",
        "state": expected_state,
        "sizeBytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }
    assert len(blobs.tag_reads) == 1


@pytest.mark.parametrize("scope", ["other-owner", "other-tenant", "other-session", "missing", "anonymous"])
def test_upload_status_is_owner_and_session_scoped(
    status_client: tuple[TestClient, str, TaggedBlobStore], scope: str
) -> None:
    client, session_id, blobs = status_client
    uploaded = client.post(
        f"/api/sessions/{session_id}/uploads",
        files={"upload": ("data.csv", b"value\n42\n", "text/csv")},
        headers={"Origin": "https://analyst.example.test", "X-CSRF-Token": "known-csrf"},
    )
    assert uploaded.status_code == 202
    upload_id = uploaded.json()["uploadId"]
    if scope in {"other-owner", "other-tenant"}:
        client.cookies.set("eda_session", f"auth_upload_{scope}_1234567890")
    elif scope == "other-session":
        session_id = "ses_other1234567890"
    elif scope == "missing":
        upload_id = "upl_missing1234567890"
    else:
        client.cookies.clear()

    response = client.get(f"/api/sessions/{session_id}/uploads/{upload_id}")

    assert response.status_code == (401 if scope == "anonymous" else 404)
    assert blobs.tag_reads == []


@pytest.mark.parametrize("error_type", [ResourceNotFoundError, FileNotFoundError])
def test_missing_quarantine_blob_returns_terminal_status_instead_of_500(
    status_client: tuple[TestClient, str, TaggedBlobStore], monkeypatch: pytest.MonkeyPatch, error_type: type[Exception]
) -> None:
    client, session_id, blobs = status_client
    uploaded = client.post(
        f"/api/sessions/{session_id}/uploads",
        files={"upload": ("data.csv", b"value\n42\n", "text/csv")},
        headers={"Origin": "https://analyst.example.test", "X-CSRF-Token": "known-csrf"},
    )
    assert uploaded.status_code == 202
    upload_id = uploaded.json()["uploadId"]

    async def missing_tags(name: str) -> dict[str, str]:
        blobs.tag_reads.append(name)
        raise error_type("quarantine blob is unavailable")

    monkeypatch.setattr(blobs, "get_tags", missing_tags)
    for _ in range(2):
        response = client.get(f"/api/sessions/{session_id}/uploads/{upload_id}")
        assert response.status_code == 200
        assert response.json()["state"] == "scan_failed"
        assert set(response.json()) == {"uploadId", "displayName", "state", "sizeBytes", "sha256"}
    assert len(blobs.tag_reads) == 2
