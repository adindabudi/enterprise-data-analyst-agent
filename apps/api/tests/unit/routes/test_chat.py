from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from uuid import UUID

import pytest
from eda_api.auth.models import AuthSessionRecord, Principal
from eda_api.auth.repository import InMemoryAuthRepository
from eda_api.chat.service import InteractiveChatUpdate
from eda_api.config import Settings
from eda_api.hosted_responses import HostedResponseAttempt, HostedResponseStatus
from eda_api.main import create_app
from eda_api.storage.uploads import InMemoryBlobStore, InMemoryUploadRepository, UploadService
from eda_api.storage.workspace import InMemoryWorkspaceRepository
from eda_api.task_service import NullTaskEventStore
from eda_runtime_state.messages import InMemoryMessageRepository
from eda_runtime_state.models import TaskPartition
from eda_runtime_state.tasks import InMemoryRuntimeStateRepository
from fastapi.testclient import TestClient


class FakeDurableClient:
    def __init__(self) -> None:
        self.scheduled = 0

    async def start(
        self,
        task_id: str,
        *,
        user_identity: str,
        previous_response_id: str | None = None,
    ) -> HostedResponseAttempt:
        del user_identity, previous_response_id
        self.scheduled += 1
        return HostedResponseAttempt(id=f"resp_{task_id[5:]}", status=HostedResponseStatus.QUEUED)

    async def get(self, response_id: str, *, user_identity: str) -> HostedResponseAttempt:
        del user_identity
        return HostedResponseAttempt(id=response_id, status=HostedResponseStatus.IN_PROGRESS)

    async def cancel(self, response_id: str, *, user_identity: str) -> HostedResponseAttempt:
        del user_identity
        return HostedResponseAttempt(id=response_id, status=HostedResponseStatus.CANCELLED)

    async def close(self) -> None:
        return None


class FakeInteractiveChatService:
    def __init__(self) -> None:
        self.calls: list[tuple[TaskPartition, str, tuple[dict[str, str], ...], str]] = []

    async def stream(
        self,
        *,
        partition: TaskPartition,
        message_id: str,
        history: tuple[dict[str, str], ...],
        idempotency_key: str,
    ) -> AsyncIterator[InteractiveChatUpdate]:
        self.calls.append((partition, message_id, history, idempotency_key))
        yield InteractiveChatUpdate(event="status", data={"message": "Agent is thinking"})
        yield InteractiveChatUpdate(event="delta", data={"text": "Hello"})
        yield InteractiveChatUpdate(event="completed", data={"messageId": "msg_assistant_12345678"})


@dataclass
class AuthenticatedClient:
    client: TestClient
    headers: dict[str, str]
    durable: FakeDurableClient
    chat: FakeInteractiveChatService


def principal(owner: str) -> Principal:
    return Principal(
        tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
        owner_object_id=UUID(owner),
        audience=UUID("33333333-3333-3333-3333-333333333333"),
    )


def authenticated_client(
    settings: Settings,
    workspace: InMemoryWorkspaceRepository,
    owner: Principal,
) -> AuthenticatedClient:
    auth = InMemoryAuthRepository()
    csrf = "known-csrf-token"
    auth_session = AuthSessionRecord.create(
        owner,
        csrf_token=csrf,
        ttl_seconds=300,
        id=f"auth_{owner.owner_object_id.hex}_chat",
    )
    asyncio.run(auth.put_session(auth_session))
    durable = FakeDurableClient()
    chat = FakeInteractiveChatService()
    app = create_app(
        settings_override=settings,
        auth_repository_override=auth,
        workspace_repository_override=workspace,
        upload_service_override=UploadService(
            blob_store=InMemoryBlobStore(),
            upload_repository=InMemoryUploadRepository(),
            upload_limit_bytes=settings.upload_limit_bytes,
        ),
        runtime_repository_override=InMemoryRuntimeStateRepository(),
        event_store_override=NullTaskEventStore(),
        message_repository_override=InMemoryMessageRepository(),
        hosted_client_override=durable,
        interactive_chat_service_override=chat,
    )
    client = TestClient(app, base_url=str(settings.public_origin), raise_server_exceptions=False)
    client.__enter__()
    client.cookies.set("eda_session", auth_session.id)
    client.cookies.set("eda_csrf", csrf)
    return AuthenticatedClient(
        client=client,
        headers={"Origin": str(settings.public_origin).rstrip("/"), "X-CSRF-Token": csrf},
        durable=durable,
        chat=chat,
    )


@pytest.fixture
def workspace() -> InMemoryWorkspaceRepository:
    return InMemoryWorkspaceRepository()


@pytest.fixture
def owner_client(settings: Settings, workspace: InMemoryWorkspaceRepository) -> Iterator[AuthenticatedClient]:
    value = authenticated_client(settings, workspace, principal("22222222-2222-2222-2222-222222222222"))
    try:
        yield value
    finally:
        value.client.__exit__(None, None, None)


@pytest.fixture
def other_client(settings: Settings, workspace: InMemoryWorkspaceRepository) -> Iterator[AuthenticatedClient]:
    value = authenticated_client(settings, workspace, principal("44444444-4444-4444-4444-444444444444"))
    try:
        yield value
    finally:
        value.client.__exit__(None, None, None)


def create_session_and_message(value: AuthenticatedClient) -> tuple[str, str]:
    session = value.client.post("/api/sessions", json={"title": "Private chat"}, headers=value.headers)
    assert session.status_code == 201
    session_id = session.json()["sessionId"]
    message = value.client.post(
        f"/api/sessions/{session_id}/messages",
        json={"text": "Explain decimal precision"},
        headers={**value.headers, "Idempotency-Key": "chat-message-0001"},
    )
    assert message.status_code == 201
    return session_id, message.json()["messageId"]


def test_interactive_chat_streams_without_durable_task(owner_client: AuthenticatedClient) -> None:
    session_id, message_id = create_session_and_message(owner_client)

    response = owner_client.client.post(
        f"/api/sessions/{session_id}/chat",
        json={
            "messageId": message_id,
            "history": [{"role": "assistant", "text": "Prior bounded context."}],
        },
        headers={**owner_client.headers, "Idempotency-Key": "chat-turn-0001"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: status" in response.text
    assert "event: delta" in response.text
    assert "event: completed" in response.text
    assert owner_client.durable.scheduled == 0
    assert owner_client.chat.calls[0][1:] == (
        message_id,
        ({"role": "assistant", "text": "Prior bounded context."},),
        "chat-turn-0001",
    )


def test_interactive_chat_requires_csrf(owner_client: AuthenticatedClient) -> None:
    session_id, message_id = create_session_and_message(owner_client)

    response = owner_client.client.post(
        f"/api/sessions/{session_id}/chat",
        json={"messageId": message_id, "history": []},
        headers={"Idempotency-Key": "chat-turn-0002"},
    )

    assert response.status_code == 403
    assert owner_client.chat.calls == []


def test_interactive_chat_rejects_history_over_the_aggregate_context_limit(
    owner_client: AuthenticatedClient,
) -> None:
    session_id, message_id = create_session_and_message(owner_client)

    response = owner_client.client.post(
        f"/api/sessions/{session_id}/chat",
        json={
            "messageId": message_id,
            "history": [
                {"role": "user" if index % 2 == 0 else "assistant", "text": str(index) * 4_000} for index in range(4)
            ],
        },
        headers={**owner_client.headers, "Idempotency-Key": "chat-turn-context-limit-0001"},
    )

    assert response.status_code == 422
    assert owner_client.chat.calls == []


def test_interactive_chat_hides_other_owner_session(
    owner_client: AuthenticatedClient,
    other_client: AuthenticatedClient,
) -> None:
    session_id, message_id = create_session_and_message(owner_client)

    response = other_client.client.post(
        f"/api/sessions/{session_id}/chat",
        json={"messageId": message_id, "history": []},
        headers={**other_client.headers, "Idempotency-Key": "chat-turn-0003"},
    )

    assert response.status_code == 404
    assert other_client.chat.calls == []
