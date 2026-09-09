from __future__ import annotations

import asyncio
from dataclasses import dataclass
from uuid import UUID

import pytest
from eda_api.auth.models import AuthSessionRecord, Principal
from eda_api.auth.repository import InMemoryAuthRepository
from eda_api.config import Settings
from eda_api.hosted_responses import HostedResponseAttempt, HostedResponseStatus
from eda_api.main import create_app
from eda_api.storage.uploads import InMemoryBlobStore, InMemoryUploadRepository, UploadService
from eda_api.storage.workspace import InMemoryWorkspaceRepository
from eda_api.task_service import NullTaskEventStore
from eda_runtime_state.messages import InMemoryMessageRepository
from eda_runtime_state.tasks import InMemoryRuntimeStateRepository
from fastapi.testclient import TestClient


class FakeDurableClient:
    def __init__(self) -> None:
        self.scheduled: list[tuple[str, str, str | None]] = []
        self.events: list[tuple[str, str, dict[str, object]]] = []

    async def start(
        self,
        task_id: str,
        *,
        user_identity: str,
        previous_response_id: str | None = None,
    ) -> HostedResponseAttempt:
        self.scheduled.append((task_id, user_identity, previous_response_id))
        return HostedResponseAttempt(id=f"resp_{task_id[5:]}", status=HostedResponseStatus.QUEUED)

    async def get(self, response_id: str, *, user_identity: str) -> HostedResponseAttempt:
        del user_identity
        return HostedResponseAttempt(id=response_id, status=HostedResponseStatus.IN_PROGRESS)

    async def cancel(self, response_id: str, *, user_identity: str) -> HostedResponseAttempt:
        self.events.append((response_id, "cancel", {"userIdentity": user_identity}))
        return HostedResponseAttempt(id=response_id, status=HostedResponseStatus.CANCELLED)

    async def close(self) -> None:
        return None


@dataclass
class ControlClient:
    client: TestClient
    csrf_headers: dict[str, str]
    workspace: InMemoryWorkspaceRepository
    messages: InMemoryMessageRepository
    runtime: InMemoryRuntimeStateRepository
    durable: FakeDurableClient
    principal: Principal


def principal(owner: str) -> Principal:
    return Principal(
        tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
        owner_object_id=UUID(owner),
        audience=UUID("33333333-3333-3333-3333-333333333333"),
    )


def open_control_client(settings: Settings, msal_client: object, owner: Principal) -> ControlClient:
    auth = InMemoryAuthRepository()
    workspace = InMemoryWorkspaceRepository()
    messages = InMemoryMessageRepository()
    runtime = InMemoryRuntimeStateRepository()
    durable = FakeDurableClient()
    csrf = "known-csrf-token"
    session = AuthSessionRecord.create(owner, csrf_token=csrf, ttl_seconds=300, id=f"auth_{owner.owner_object_id.hex}")
    asyncio.run(auth.put_session(session))
    application = create_app(
        settings_override=settings,
        auth_repository_override=auth,
        msal_override=msal_client,
        workspace_repository_override=workspace,
        upload_service_override=UploadService(
            blob_store=InMemoryBlobStore(),
            upload_repository=InMemoryUploadRepository(),
            upload_limit_bytes=settings.upload_limit_bytes,
        ),
        runtime_repository_override=runtime,
        event_store_override=NullTaskEventStore(),
        message_repository_override=messages,
        hosted_client_override=durable,
    )
    client = TestClient(application, base_url="https://analyst.example.test", raise_server_exceptions=False)
    client.__enter__()
    client.cookies.set("eda_session", session.id)
    client.cookies.set("eda_csrf", csrf)
    return ControlClient(
        client=client,
        csrf_headers={"Origin": "https://analyst.example.test", "X-CSRF-Token": csrf},
        workspace=workspace,
        messages=messages,
        runtime=runtime,
        durable=durable,
        principal=owner,
    )


@pytest.fixture
def control_client(settings: Settings, msal_client: object):
    value = open_control_client(settings, msal_client, principal("22222222-2222-2222-2222-222222222222"))
    try:
        yield value
    finally:
        value.client.__exit__(None, None, None)


def create_session(value: ControlClient) -> str:
    response = value.client.post("/api/sessions", json={"title": "Revenue"}, headers=value.csrf_headers)
    assert response.status_code == 201
    return response.json()["sessionId"]


def create_message(value: ControlClient, session_id: str) -> str:
    response = value.client.post(
        f"/api/sessions/{session_id}/messages",
        json={"text": "Analyze APAC revenue"},
        headers={**value.csrf_headers, "Idempotency-Key": "message-request-1"},
    )
    assert response.status_code == 201
    return response.json()["messageId"]


def test_message_and_task_creation_are_idempotent_and_polling_is_filtered(control_client: ControlClient) -> None:
    session_id = create_session(control_client)
    message_id = create_message(control_client, session_id)
    retried_message = create_message(control_client, session_id)

    headers = {**control_client.csrf_headers, "Idempotency-Key": "task-request-1"}
    first = control_client.client.post(
        f"/api/sessions/{session_id}/tasks", json={"messageId": message_id}, headers=headers
    )
    retried = control_client.client.post(
        f"/api/sessions/{session_id}/tasks", json={"messageId": message_id}, headers=headers
    )

    assert retried_message == message_id
    assert first.status_code == retried.status_code == 202
    assert first.json()["taskId"] == retried.json()["taskId"]
    task_id = first.json()["taskId"]
    polled = control_client.client.get(f"/api/tasks/{task_id}")
    assert polled.status_code == 200
    assert set(polled.json()) == {
        "taskId",
        "sessionId",
        "status",
        "checkpointSequence",
        "activeAttemptId",
        "finalMessageId",
    }
    assert {"tenantId", "ownerObjectId", "sourceMessageId", "_etag"}.isdisjoint(polled.json())


def test_steer_and_cancel_persist_then_cancel_the_hosted_attempt(control_client: ControlClient) -> None:
    session_id = create_session(control_client)
    message_id = create_message(control_client, session_id)
    task = control_client.client.post(
        f"/api/sessions/{session_id}/tasks",
        json={"messageId": message_id},
        headers={**control_client.csrf_headers, "Idempotency-Key": "task-request-2"},
    ).json()
    task_id = task["taskId"]

    steered = control_client.client.post(
        f"/api/tasks/{task_id}/steer",
        json={"instruction": "Check APAC"},
        headers={**control_client.csrf_headers, "Idempotency-Key": "steer-request-1"},
    )
    cancelled = control_client.client.post(
        f"/api/tasks/{task_id}/cancel",
        headers={**control_client.csrf_headers, "Idempotency-Key": "cancel-request-1"},
    )

    assert steered.status_code == cancelled.status_code == 202
    assert steered.json()["sequence"] == 1
    assert cancelled.json()["cancellationRequested"] is True
    stored = asyncio.run(control_client.runtime.resolve_task(task_id))
    assert stored is not None and stored.cancellation_requested is True
    assert [event[1] for event in control_client.durable.events] == ["cancel"]


def test_controls_require_csrf_idempotency_and_strict_bodies(control_client: ControlClient) -> None:
    session_id = create_session(control_client)
    missing_key = control_client.client.post(
        f"/api/sessions/{session_id}/messages",
        json={"text": "Analyze", "tenantId": "11111111-1111-1111-1111-111111111111"},
        headers=control_client.csrf_headers,
    )
    invalid_key = control_client.client.post(
        f"/api/sessions/{session_id}/messages",
        json={"text": "Analyze"},
        headers={**control_client.csrf_headers, "Idempotency-Key": "has space"},
    )
    missing_csrf = control_client.client.post(
        f"/api/sessions/{session_id}/messages",
        json={"text": "Analyze"},
        headers={"Idempotency-Key": "message-request-2"},
    )

    assert missing_key.status_code == 422
    assert invalid_key.status_code == 422
    assert missing_csrf.status_code == 403


def test_other_owner_cannot_use_message_or_task(
    control_client: ControlClient, settings: Settings, msal_client: object
) -> None:
    session_id = create_session(control_client)
    message_id = create_message(control_client, session_id)
    other = open_control_client(settings, msal_client, principal("44444444-4444-4444-4444-444444444444"))
    other.workspace = control_client.workspace
    other.client.app.state.workspace_repository = control_client.workspace
    try:
        response = other.client.post(
            f"/api/sessions/{session_id}/tasks",
            json={"messageId": message_id},
            headers={**other.csrf_headers, "Idempotency-Key": "task-request-other"},
        )
    finally:
        other.client.__exit__(None, None, None)

    assert response.status_code == 404
