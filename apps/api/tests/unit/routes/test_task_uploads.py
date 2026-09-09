from __future__ import annotations

import asyncio
from collections.abc import Iterator
from dataclasses import dataclass
from uuid import UUID

import pytest
from eda_api.auth.models import AuthSessionRecord
from eda_api.auth.repository import InMemoryAuthRepository
from eda_api.config import Settings
from eda_api.main import create_app
from eda_api.storage.artifacts import CosmosBlobArtifactCatalog
from eda_api.storage.workspace import InMemoryWorkspaceRepository
from eda_api.task_service import NullTaskEventStore
from fastapi.testclient import TestClient

from apps.api.tests.unit.storage.test_uploads import OWNER, chunks
from apps.api.tests.unit.test_task_uploads import Pipeline


@dataclass
class HttpPipeline:
    client: TestClient
    pipeline: Pipeline
    session_id: str


def headers(key: str = "upload-api-task-key") -> dict[str, str]:
    return {"Origin": "https://analyst.example.test", "X-CSRF-Token": "known-csrf", "Idempotency-Key": key}


@pytest.fixture
def http_pipeline(settings: Settings, msal_client: object) -> Iterator[HttpPipeline]:
    pipeline = Pipeline()
    auth = InMemoryAuthRepository()
    workspace = InMemoryWorkspaceRepository()
    csrf_token = "-".join(("known", "csrf"))
    for label, principal in [("owner", OWNER), ("other", OWNER.model_copy(update={"owner_object_id": UUID(int=9)}))]:
        session = AuthSessionRecord.create(
            principal, csrf_token=csrf_token, ttl_seconds=300, id=f"auth_input_api_{label}_12345678"
        )
        asyncio.run(auth.put_session(session))
    application = create_app(
        settings_override=settings,
        auth_repository_override=auth,
        msal_override=msal_client,
        workspace_repository_override=workspace,
        upload_service_override=pipeline.uploads,
        runtime_repository_override=pipeline.runtime,
        event_store_override=NullTaskEventStore(),
        message_repository_override=pipeline.messages,
        hosted_client_override=pipeline.hosted,
        artifact_catalog_override=CosmosBlobArtifactCatalog(pipeline.workspace, pipeline.blobs),
    )
    application.state.input_artifact_writer_override = pipeline.writer
    with TestClient(application, base_url="https://analyst.example.test", raise_server_exceptions=False) as client:
        client.cookies.set("eda_session", "auth_input_api_owner_12345678")
        client.cookies.set("eda_csrf", "known-csrf")
        session_response = client.post("/api/sessions", json={"title": "Uploaded inputs"}, headers=headers())
        assert session_response.status_code == 201
        yield HttpPipeline(client, pipeline, session_response.json()["sessionId"])


def prepare(env: HttpPipeline) -> tuple[str, str]:
    uploaded = env.client.post(
        f"/api/sessions/{env.session_id}/uploads",
        files={"upload": ("data.csv", b"value\n42\n", "text/csv")},
        headers=headers(),
    )
    assert uploaded.status_code == 202
    source = env.client.post(
        f"/api/sessions/{env.session_id}/messages",
        json={"text": "Analyze the attached data"},
        headers=headers("upload-api-message-key"),
    )
    assert source.status_code == 201
    return uploaded.json()["uploadId"], source.json()["messageId"]


def submit(env: HttpPipeline, message_id: str, upload_ids: list[str]):
    return env.client.post(
        f"/api/sessions/{env.session_id}/tasks",
        json={"messageId": message_id, "inputUploadIds": upload_ids},
        headers=headers(),
    )


@pytest.mark.parametrize(
    ("scan_result", "expected_status", "code"),
    [(None, 409, "upload_scanning"), ("Malicious", 422, "upload_rejected"), ("Error", 422, "upload_scan_failed"), ("Not scanned", 422, "upload_scan_failed")],
)
def test_task_submission_maps_authoritative_scan_states(
    http_pipeline: HttpPipeline, scan_result: str | None, expected_status: int, code: str
) -> None:
    env = http_pipeline
    upload_id, message_id = prepare(env)
    env.pipeline.quarantine.scan_result = scan_result

    response = submit(env, message_id, [upload_id])

    assert response.status_code == expected_status
    assert response.json()["code"] == code
    assert env.pipeline.hosted.started == []
    assert env.pipeline.quarantine.downloads == 0


def test_clean_upload_is_bound_listed_and_downloadable_as_input(http_pipeline: HttpPipeline) -> None:
    env = http_pipeline
    upload_id, message_id = prepare(env)

    response = submit(env, message_id, [upload_id])

    assert response.status_code == 202
    task_id = response.json()["taskId"]
    task = asyncio.run(env.pipeline.runtime.resolve_task(task_id))
    assert task is not None and len(task.input_artifacts) == 1
    listing = env.client.get(f"/api/tasks/{task_id}/artifacts")
    assert listing.status_code == 200
    artifact = listing.json()["artifacts"][0]
    assert artifact["kind"] == "input"
    assert artifact["displayName"] == "data.csv"
    content = env.client.get(f"/api/tasks/{task_id}/artifacts/{artifact['artifactId']}/versions/1/content")
    assert content.status_code == 200
    assert content.content == b"value\n42\n"
    assert content.headers["content-type"] == "application/octet-stream"
    assert content.headers["x-content-type-options"] == "nosniff"
    provenance = env.client.get(f"/api/tasks/{task_id}/provenance")
    assert provenance.status_code == 200
    assert provenance.json()["sourceQueries"] == []
    assert provenance.json()["artifacts"] == []


def test_task_submission_hides_foreign_session_uploads(http_pipeline: HttpPipeline) -> None:
    env = http_pipeline
    _, message_id = prepare(env)
    foreign = asyncio.run(
        env.pipeline.uploads.create_quarantine_upload(OWNER, "ses_foreign_session01", "data.csv", chunks([b"value\n42\n"]))
    )

    response = submit(env, message_id, [foreign.id])

    assert response.status_code == 404
    assert env.pipeline.hosted.started == []
    assert env.pipeline.quarantine.downloads == 0


@pytest.mark.parametrize("upload_ids", [["upl_12345678"] * 2, [f"upl_{index:08d}" for index in range(11)], ["../blob"], None])
def test_task_input_id_contract_is_bounded_and_strict(http_pipeline: HttpPipeline, upload_ids: object) -> None:
    env = http_pipeline
    _, message_id = prepare(env)
    response = env.client.post(
        f"/api/sessions/{env.session_id}/tasks",
        json={"messageId": message_id, "inputUploadIds": upload_ids},
        headers=headers(),
    )

    assert response.status_code == 422
    assert env.pipeline.hosted.started == []


def test_task_creation_rechecks_the_uploaded_digest(http_pipeline: HttpPipeline) -> None:
    env = http_pipeline
    upload_id, message_id = prepare(env)
    env.pipeline.quarantine.download_content = b"value\n99\n"

    response = submit(env, message_id, [upload_id])

    assert response.status_code == 422
    assert response.json()["code"] == "upload_verification_failed"
    assert env.pipeline.hosted.started == []


def test_partial_promotion_failure_returns_retriable_error(http_pipeline: HttpPipeline) -> None:
    env = http_pipeline
    upload_id, message_id = prepare(env)
    env.pipeline.workspace.fail_next_write = True

    failed = submit(env, message_id, [upload_id])
    assert failed.status_code == 503
    assert env.pipeline.hosted.started == []
    retried = submit(env, message_id, [upload_id])

    assert retried.status_code == 202
    assert len(env.pipeline.hosted.started) == 1


def test_task_idempotency_includes_upload_selection(http_pipeline: HttpPipeline) -> None:
    env = http_pipeline
    first_upload, message_id = prepare(env)
    second_upload, _ = prepare(env)

    first = submit(env, message_id, [first_upload, second_upload])
    retry = submit(env, message_id, [second_upload, first_upload])
    changed = submit(env, message_id, [])

    assert first.status_code == retry.status_code == 202
    assert first.json() == retry.json()
    assert changed.status_code == 409
    assert len(env.pipeline.hosted.started) == 1


def test_input_artifacts_remain_owner_scoped(http_pipeline: HttpPipeline) -> None:
    env = http_pipeline
    upload_id, message_id = prepare(env)
    response = submit(env, message_id, [upload_id])
    assert response.status_code == 202
    task_id = response.json()["taskId"]
    task = asyncio.run(env.pipeline.runtime.resolve_task(task_id))
    assert task is not None
    ref = task.input_artifacts[0]
    env.client.cookies.set("eda_session", "auth_input_api_other_12345678")

    assert env.client.get(f"/api/tasks/{task_id}/artifacts").status_code == 404
    assert env.client.get(f"/api/tasks/{task_id}/artifacts/{ref.artifact_id}/versions/1/content").status_code == 404


@pytest.mark.parametrize("corruption", ["blob", "metadata"])
def test_tampered_promoted_input_is_not_downloadable(http_pipeline: HttpPipeline, corruption: str) -> None:
    env = http_pipeline
    upload_id, message_id = prepare(env)
    response = submit(env, message_id, [upload_id])
    assert response.status_code == 202
    task_id = response.json()["taskId"]
    task = asyncio.run(env.pipeline.runtime.resolve_task(task_id))
    assert task is not None
    ref = task.input_artifacts[0]
    if corruption == "blob":
        next(iter(env.pipeline.blobs.items.values())).content = b"value\n99\n"
    else:
        next(iter(env.pipeline.workspace.items.values()))["blobName"] = "foreign-owner/blob"

    response = env.client.get(f"/api/tasks/{task_id}/artifacts/{ref.artifact_id}/versions/1/content")

    assert response.status_code == 404