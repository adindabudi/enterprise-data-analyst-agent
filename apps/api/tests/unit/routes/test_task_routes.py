# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from dataclasses import dataclass
from typing import cast
from uuid import UUID

import pytest
from eda_api.analysis.attempts import AttemptStatus, TaskAttempt
from eda_api.auth.models import AuthSessionRecord, Principal
from eda_api.auth.repository import InMemoryAuthRepository
from eda_api.config import Settings
from eda_api.main import create_app
from eda_api.storage.uploads import InMemoryBlobStore, InMemoryUploadRepository, UploadService
from eda_api.storage.workspace import InMemoryWorkspaceRepository
from eda_api.task_service import NullTaskEventStore, TaskService
from eda_runtime_state.messages import CanonicalMessage, InMemoryMessageRepository
from eda_runtime_state.tasks import InMemoryRuntimeStateRepository, TaskRecord
from fastapi import FastAPI
from fastapi.testclient import TestClient


class FakeDurableClient:
    def __init__(self) -> None:
        self.scheduled: list[str] = []
        self.events: list[tuple[str, str, dict[str, object]]] = []

    async def start(self, task_id: str) -> TaskAttempt:
        self.scheduled.append(task_id)
        return TaskAttempt(id=f"resp_{task_id[5:]}", status=AttemptStatus.QUEUED)

    async def get(self, response_id: str) -> TaskAttempt:
        return TaskAttempt(id=response_id, status=AttemptStatus.IN_PROGRESS)

    async def cancel(self, response_id: str) -> TaskAttempt:
        self.events.append((response_id, "cancel", {}))
        return TaskAttempt(id=response_id, status=AttemptStatus.CANCELLED)

    async def close(self) -> None:
        return None


@dataclass
class AuthenticatedClient:
    client: TestClient
    csrf_headers: dict[str, str]
    durable: FakeDurableClient
    messages: InMemoryMessageRepository
    application: FastAPI


def principal(owner_object_id: str) -> Principal:
    return Principal(
        tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
        owner_object_id=UUID(owner_object_id),
        audience=UUID("33333333-3333-3333-3333-333333333333"),
    )


def authenticated_client(
    settings: Settings,
    workspace_repository: InMemoryWorkspaceRepository,
    owner: Principal,
) -> AuthenticatedClient:
    auth_repository = InMemoryAuthRepository()
    csrf_token = "known-csrf"  # noqa: S105 - deterministic test-only CSRF fixture.
    session = AuthSessionRecord.create(
        owner,
        csrf_token=csrf_token,
        ttl_seconds=300,
        id=f"auth_{owner.owner_object_id.hex}_session",
    )
    asyncio.run(auth_repository.put_session(session))
    durable = FakeDurableClient()
    messages = InMemoryMessageRepository()
    application = create_app(
        settings_override=settings,
        auth_repository_override=auth_repository,
        workspace_repository_override=workspace_repository,
        upload_service_override=UploadService(
            blob_store=InMemoryBlobStore(),
            upload_repository=InMemoryUploadRepository(),
            upload_limit_bytes=settings.upload_limit_bytes,
        ),
        runtime_repository_override=InMemoryRuntimeStateRepository(),
        event_store_override=NullTaskEventStore(),
        task_executor_override=durable,
        message_repository_override=messages,
    )
    test_client = TestClient(application, base_url="https://analyst.example.test", raise_server_exceptions=False)
    test_client.__enter__()
    test_client.cookies.set("eda_session", session.id)
    test_client.cookies.set("eda_csrf", csrf_token)
    return AuthenticatedClient(
        client=test_client,
        csrf_headers={"Origin": "https://analyst.example.test", "X-CSRF-Token": csrf_token},
        durable=durable,
        messages=messages,
        application=application,
    )


@pytest.fixture
def workspace_repository() -> InMemoryWorkspaceRepository:
    return InMemoryWorkspaceRepository()


@pytest.fixture
def owner_client(
    settings: Settings, workspace_repository: InMemoryWorkspaceRepository
) -> Iterator[AuthenticatedClient]:
    value = authenticated_client(
        settings,
        workspace_repository,
        principal("22222222-2222-2222-2222-222222222222"),
    )
    try:
        yield value
    finally:
        value.client.__exit__(None, None, None)


@pytest.fixture
def other_owner_client(
    settings: Settings, workspace_repository: InMemoryWorkspaceRepository
) -> Iterator[AuthenticatedClient]:
    value = authenticated_client(
        settings,
        workspace_repository,
        principal("44444444-4444-4444-4444-444444444444"),
    )
    try:
        yield value
    finally:
        value.client.__exit__(None, None, None)


def _stored_task(owner_client: AuthenticatedClient, task_id: str) -> TaskRecord:
    service = cast(TaskService, owner_client.application.state.task_service)
    task = asyncio.run(service.repository.resolve_task(task_id))
    assert task is not None
    return task


def create_private_session(owner_client: AuthenticatedClient) -> str:
    response = owner_client.client.post(
        "/api/sessions",
        json={"title": "Private analysis"},
        headers=owner_client.csrf_headers,
    )
    assert response.status_code == 201
    return response.json()["sessionId"]


def create_task(owner_client: AuthenticatedClient, session_id: str) -> str:
    message_response = owner_client.client.post(
        f"/api/sessions/{session_id}/messages",
        json={"text": "Analyze FY2026 variance"},
        headers={**owner_client.csrf_headers, "Idempotency-Key": "idem-message-0001"},
    )
    assert message_response.status_code == 201
    message_id = message_response.json()["messageId"]

    task_response = owner_client.client.post(
        f"/api/sessions/{session_id}/tasks",
        json={"messageId": message_id},
        headers={**owner_client.csrf_headers, "Idempotency-Key": "idem-task-0001"},
    )
    assert task_response.status_code == 202
    return task_response.json()["taskId"]


def test_missing_idempotency_headers_are_rejected(owner_client: AuthenticatedClient) -> None:
    session_id = create_private_session(owner_client)

    response = owner_client.client.post(
        f"/api/sessions/{session_id}/messages",
        json={"text": "Analyze"},
        headers=owner_client.csrf_headers,
    )

    assert response.status_code == 422


def test_message_and_task_bodies_are_strict(owner_client: AuthenticatedClient) -> None:
    session_id = create_private_session(owner_client)

    message_response = owner_client.client.post(
        f"/api/sessions/{session_id}/messages",
        json={"text": "Analyze", "ownerObjectId": "forbidden"},
        headers={**owner_client.csrf_headers, "Idempotency-Key": "idem-message-0002"},
    )
    task_response = owner_client.client.post(
        f"/api/sessions/{session_id}/tasks",
        json={"messageId": "msg_12345678", "tenantId": "forbidden"},
        headers={**owner_client.csrf_headers, "Idempotency-Key": "idem-task-0002"},
    )

    assert message_response.status_code == 422
    assert task_response.status_code == 422


def test_other_owner_gets_not_found_for_private_session(
    owner_client: AuthenticatedClient,
    other_owner_client: AuthenticatedClient,
) -> None:
    session_id = create_private_session(owner_client)

    response = other_owner_client.client.post(
        f"/api/sessions/{session_id}/messages",
        json={"text": "Analyze"},
        headers={**other_owner_client.csrf_headers, "Idempotency-Key": "idem-message-0003"},
    )

    assert response.status_code == 404


def test_message_and_task_creation_are_idempotent(owner_client: AuthenticatedClient) -> None:
    session_id = create_private_session(owner_client)

    message_headers = {**owner_client.csrf_headers, "Idempotency-Key": "idem-message-0004"}
    first_message = owner_client.client.post(
        f"/api/sessions/{session_id}/messages",
        json={"text": "Analyze FY2026 variance"},
        headers=message_headers,
    )
    second_message = owner_client.client.post(
        f"/api/sessions/{session_id}/messages",
        json={"text": "Analyze FY2026 variance"},
        headers=message_headers,
    )

    assert first_message.status_code == 201
    assert second_message.status_code == 201
    assert first_message.json() == second_message.json()

    task_headers = {**owner_client.csrf_headers, "Idempotency-Key": "idem-task-0004"}
    first_task = owner_client.client.post(
        f"/api/sessions/{session_id}/tasks",
        json={"messageId": first_message.json()["messageId"]},
        headers=task_headers,
    )
    second_task = owner_client.client.post(
        f"/api/sessions/{session_id}/tasks",
        json={"messageId": first_message.json()["messageId"]},
        headers=task_headers,
    )

    assert first_task.status_code == 202
    assert second_task.status_code == 202
    assert first_task.json() == second_task.json()


def test_polling_returns_public_fields_only(owner_client: AuthenticatedClient) -> None:
    session_id = create_private_session(owner_client)
    task_id = create_task(owner_client, session_id)

    response = owner_client.client.get(f"/api/tasks/{task_id}")

    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {
        "taskId",
        "sessionId",
        "status",
        "checkpointSequence",
        "activeAttemptId",
        "finalMessageId",
    }


def test_final_message_is_owner_scoped_and_filtered(owner_client: AuthenticatedClient) -> None:
    session_id = create_private_session(owner_client)
    task_id = create_task(owner_client, session_id)
    task = asyncio.run(owner_client.client.app.state.runtime_repository.resolve_task(task_id))
    assert task is not None
    message = CanonicalMessage(
        id="msg_final_12345678",
        tenant_id=str(task.tenant_id),
        owner_object_id=str(task.owner_object_id),
        session_id=task.session_id,
        task_id=task.id,
        role="assistant",
        text="Authoritative final answer.",
        created_at=task.created_at,
    )
    asyncio.run(owner_client.messages.append_canonical(message))
    asyncio.run(owner_client.client.app.state.runtime_repository.set_final_message(task_id, message.id))

    response = owner_client.client.get(f"/api/tasks/{task_id}/messages/{message.id}")

    assert response.status_code == 200
    assert response.json() == {
        "messageId": message.id,
        "role": "assistant",
        "text": "Authoritative final answer.",
    }


def test_steer_and_cancel_require_idempotency_and_cancel_the_running_attempt(
    owner_client: AuthenticatedClient,
) -> None:
    session_id = create_private_session(owner_client)
    task_id = create_task(owner_client, session_id)

    missing_header = owner_client.client.post(
        f"/api/tasks/{task_id}/steer",
        json={"instruction": "Focus on APAC"},
        headers=owner_client.csrf_headers,
    )
    steer_response = owner_client.client.post(
        f"/api/tasks/{task_id}/steer",
        json={"instruction": "Focus on APAC"},
        headers={**owner_client.csrf_headers, "Idempotency-Key": "idem-steer-0001"},
    )
    cancel_response = owner_client.client.post(
        f"/api/tasks/{task_id}/cancel",
        headers={**owner_client.csrf_headers, "Idempotency-Key": "idem-cancel-0001"},
    )

    assert missing_header.status_code == 422
    assert steer_response.status_code == 202
    assert cancel_response.status_code == 202
    assert [event[1] for event in owner_client.durable.events] == ["cancel"]


def test_the_worker_receives_the_conversation_a_task_was_started_from(
    owner_client: AuthenticatedClient,
) -> None:
    session_id = create_private_session(owner_client)
    message_response = owner_client.client.post(
        f"/api/sessions/{session_id}/messages",
        json={"text": "tarikin data 38 pasien terus export ke excel"},
        headers={**owner_client.csrf_headers, "Idempotency-Key": "idem-message-0010"},
    )
    assert message_response.status_code == 201

    task_response = owner_client.client.post(
        f"/api/sessions/{session_id}/tasks",
        json={
            "messageId": message_response.json()["messageId"],
            "history": [
                {"role": "user", "text": "ICU mana yang okupansinya di atas 75%?"},
                {"role": "assistant", "text": "Empat ICU. 38 dari 308 pasien."},
            ],
        },
        headers={**owner_client.csrf_headers, "Idempotency-Key": "idem-task-0010"},
    )

    assert task_response.status_code == 202
    task = _stored_task(owner_client, task_response.json()["taskId"])
    # "38 pasien" means nothing to the worker unless the turn that produced it travels with the task.
    assert task.handoff_context is not None
    assert "38 dari 308 pasien" in task.handoff_context


def test_a_task_started_without_a_conversation_carries_none(
    owner_client: AuthenticatedClient,
) -> None:
    session_id = create_private_session(owner_client)
    task_id = create_task(owner_client, session_id)

    assert _stored_task(owner_client, task_id).handoff_context is None


def test_an_oversized_conversation_is_refused(owner_client: AuthenticatedClient) -> None:
    session_id = create_private_session(owner_client)
    message_response = owner_client.client.post(
        f"/api/sessions/{session_id}/messages",
        json={"text": "Analyze"},
        headers={**owner_client.csrf_headers, "Idempotency-Key": "idem-message-0011"},
    )
    message_id = message_response.json()["messageId"]

    too_long = owner_client.client.post(
        f"/api/sessions/{session_id}/tasks",
        json={"messageId": message_id, "history": [{"role": "user", "text": "x" * 4000} for _ in range(4)]},
        headers={**owner_client.csrf_headers, "Idempotency-Key": "idem-task-0011"},
    )
    too_many = owner_client.client.post(
        f"/api/sessions/{session_id}/tasks",
        json={"messageId": message_id, "history": [{"role": "user", "text": "hi"} for _ in range(11)]},
        headers={**owner_client.csrf_headers, "Idempotency-Key": "idem-task-0012"},
    )

    assert too_long.status_code == 422
    assert too_many.status_code == 422
