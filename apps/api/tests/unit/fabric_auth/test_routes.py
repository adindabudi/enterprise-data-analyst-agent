from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from http.cookies import SimpleCookie
from uuid import UUID

import pytest
from eda_api.auth.models import AuthSessionRecord, Principal
from eda_api.auth.repository import InMemoryAuthRepository
from eda_api.config import Settings
from eda_api.dependencies import settings as settings_dependency
from eda_api.fabric_auth.service import FabricAuthCoordinator
from eda_api.main import create_app
from eda_api.readiness.models import FabricPackStatus
from eda_api.storage.uploads import InMemoryBlobStore, InMemoryUploadRepository, UploadService
from eda_api.storage.workspace import InMemoryWorkspaceRepository
from eda_api.task_service import NullTaskEventStore
from eda_contracts.tasks import TaskStatus
from eda_fabric_auth import (
    CipherEnvelope,
    EnvelopeCipher,
    EnvelopeContext,
    FabricGrantRecord,
    FabricGrantState,
    FabricPendingGrantRecord,
    FabricProvider,
    InMemoryFabricGrantRepository,
    audience_hash,
    provider_scope_hash,
)
from eda_runtime_state.models import TaskRecord
from eda_runtime_state.tasks import InMemoryRuntimeStateRepository
from fastapi.testclient import TestClient


class _NoopMsalClient:
    async def initiate(self) -> dict[str, object]:
        return {
            "state": "state-auth-unused",
            "auth_uri": "https://login.microsoftonline.com/11111111-1111-1111-1111-111111111111/oauth2/v2.0/authorize",
        }

    async def complete(self, flow: dict[str, object], response: dict[str, str]) -> Principal:
        del flow, response
        return Principal(
            tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
            owner_object_id=UUID("22222222-2222-2222-2222-222222222222"),
            audience=UUID("33333333-3333-3333-3333-333333333333"),
        )

    def close(self) -> None:
        return None


class _FakeFabricClient:
    def __init__(self, fabric_tenant_id: UUID, cipher: EnvelopeCipher) -> None:
        self._fabric_tenant_id = fabric_tenant_id
        self._cipher = cipher
        self._receipt_counter = 0

    async def initiate(self, *, provider: FabricProvider, redirect_uri: str) -> dict[str, object]:
        del provider, redirect_uri
        return {
            "state": "state_fabric_12345678",
            "auth_uri": (
                f"https://login.microsoftonline.com/{self._fabric_tenant_id}/oauth2/v2.0/authorize?client_id=test"
            ),
            "code_verifier": "opaque",
        }

    async def complete(
        self,
        *,
        tenant_id: UUID,
        owner_object_id: UUID,
        provider: FabricProvider,
        flow: dict[str, object],
        auth_response: dict[str, str],
    ) -> FabricPendingGrantRecord:
        assert flow["state"] == auth_response["state"]
        assert "code" in auth_response
        self._receipt_counter += 1
        receipt = f"receipt_{self._receipt_counter:08d}"
        record_id = FabricPendingGrantRecord.make_id(provider, receipt)
        cache = await self._cipher.encrypt(
            b"cache-state",
            EnvelopeContext(
                product_tenant_id=tenant_id,
                owner_object_id=owner_object_id,
                record_type="fabricPendingGrant",
                record_id=record_id,
            ),
        )
        return FabricPendingGrantRecord(
            id=record_id,
            tenant_id=tenant_id,
            owner_object_id=owner_object_id,
            provider=provider,
            receipt=receipt,
            scope_hash=provider_scope_hash(provider),
            audience_hash=audience_hash(),
            fabric_tenant_id=self._fabric_tenant_id,
            account_hash="a" * 64,
            cache=cache,
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
        )


class _PassthroughKeyWrapper:
    key_id = "https://vault.example/keys/fabric-wrap/1"

    async def wrap_key(self, key: bytes) -> bytes:
        return key

    async def unwrap_key(self, wrapped_key: bytes, key_id: str) -> bytes:
        if key_id != self.key_id:
            raise ValueError("key mismatch")
        return wrapped_key


class _FakeTaskService:
    def __init__(self, tasks: list[TaskRecord]) -> None:
        self._tasks = {task.id: task for task in tasks}
        self.resume_calls: list[tuple[str, str]] = []

    async def get_owned_task(self, principal: Principal, task_id: str) -> TaskRecord | None:
        task = self._tasks.get(task_id)
        if task is None:
            return None
        if task.tenant_id != principal.tenant_id or task.owner_object_id != principal.owner_object_id:
            return None
        return task

    async def resume_auth(self, partition: object, task_id: str, receipt: str) -> None:
        del partition
        self.resume_calls.append((task_id, receipt))

    def set_checkpoint(self, task_id: str, checkpoint: int) -> None:
        task = self._tasks[task_id]
        self._tasks[task_id] = task.model_copy(update={"checkpoint_sequence": checkpoint})


class _FakeDurableClient:
    async def schedule_new_orchestration(
        self,
        name: str,
        *,
        input: dict[str, str],
        instance_id: str,
        tags: dict[str, str] | None = None,
    ) -> object:
        del name, input, instance_id, tags
        return None

    async def raise_orchestration_event(self, instance_id: str, event_name: str, data: dict[str, object]) -> object:
        del instance_id, event_name, data
        return None

    async def close(self) -> object:
        return None


@pytest.fixture
def settings() -> Settings:
    return Settings.model_validate(
        {
            "public_origin": "https://analyst.example.test",
            "entra_tenant_id": "11111111-1111-1111-1111-111111111111",
            "entra_client_id": "33333333-3333-3333-3333-333333333333",
            "entra_client_secret": "local-only",
            "cosmos_endpoint": "https://enterprise-data-analyst.documents.azure.com",
            "blob_account_url": "https://enterprisedataanalyst.blob.core.windows.net",
            "redis_url": "redis://127.0.0.1:6379/0",
            "foundry_project_endpoint": "https://example.services.ai.azure.com/api/projects/example",
            "foundry_model_deployment": "analysis-opus",
            "fabric_enabled": True,
            "fabric_provider": "semantic_model",
            "fabric_tenant_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "fabric_client_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            "fabric_key_vault_url": "https://fabric.vault.azure.net",
            "fabric_signing_certificate_name": "fabric-oauth-signing",
            "fabric_cache_wrap_key_name": "fabric-cache-wrap",
        }
    )


@pytest.fixture
def owners() -> tuple[Principal, Principal]:
    return (
        Principal(
            tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
            owner_object_id=UUID("22222222-2222-2222-2222-222222222222"),
            audience=UUID("33333333-3333-3333-3333-333333333333"),
        ),
        Principal(
            tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
            owner_object_id=UUID("44444444-4444-4444-4444-444444444444"),
            audience=UUID("33333333-3333-3333-3333-333333333333"),
        ),
    )


@pytest.fixture
def fabric_stack(
    settings: Settings,
    owners: tuple[Principal, Principal],
) -> tuple[TestClient, InMemoryFabricGrantRepository, _FakeTaskService]:
    now = datetime.now(UTC)
    blocked_task = TaskRecord(
        id="task_blocked1234",
        tenant_id=owners[0].tenant_id,
        owner_object_id=owners[0].owner_object_id,
        session_id="ses_1234567890abcdef",
        status=TaskStatus.BLOCKED_AUTH,
        checkpoint_sequence=3,
        command_sequence=0,
        applied_command_sequence=0,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(days=1),
    )
    active_task = blocked_task.model_copy(
        update={"id": "task_active12345", "status": TaskStatus.ACQUIRING_DATA, "checkpoint_sequence": 1}
    )
    done_task = blocked_task.model_copy(
        update={"id": "task_done123456", "status": TaskStatus.COMPLETED, "checkpoint_sequence": 7}
    )
    foreign_task = blocked_task.model_copy(
        update={"id": "task_foreign123", "owner_object_id": owners[1].owner_object_id, "checkpoint_sequence": 2}
    )
    task_service = _FakeTaskService([blocked_task, active_task, done_task, foreign_task])
    repository = InMemoryFabricGrantRepository()
    cipher = EnvelopeCipher(_PassthroughKeyWrapper())
    coordinator = FabricAuthCoordinator(
        settings=settings,
        provider=FabricProvider.SEMANTIC_MODEL,
        client=_FakeFabricClient(UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"), cipher),
        repository=repository,
        cipher=cipher,
        task_service=task_service,
    )
    auth_repository = InMemoryAuthRepository()
    csrf_a = "csrf-a"
    csrf_b = "csrf-b"
    session_a = AuthSessionRecord.create(
        owners[0],
        csrf_token=csrf_a,
        ttl_seconds=1800,
        id="auth_owner_a_1234567890",
    )
    session_b = AuthSessionRecord.create(
        owners[1],
        csrf_token=csrf_b,
        ttl_seconds=1800,
        id="auth_owner_b_1234567890",
    )
    asyncio.run(auth_repository.put_session(session_a))
    asyncio.run(auth_repository.put_session(session_b))
    app = create_app(
        settings_override=settings,
        auth_repository_override=auth_repository,
        msal_override=_NoopMsalClient(),
        workspace_repository_override=InMemoryWorkspaceRepository(),
        upload_service_override=UploadService(
            blob_store=InMemoryBlobStore(),
            upload_repository=InMemoryUploadRepository(),
            upload_limit_bytes=settings.upload_limit_bytes,
        ),
        runtime_repository_override=InMemoryRuntimeStateRepository(),
        event_store_override=NullTaskEventStore(),
        hosted_client_override=_FakeDurableClient(),
        fabric_auth_service_override=coordinator,
        fabric_readiness_override=FabricPackStatus.READY,
    )
    with TestClient(app, base_url="https://analyst.example.test", raise_server_exceptions=False) as client:
        yield client, repository, task_service


def _headers(csrf: str) -> dict[str, str]:
    return {"Origin": "https://analyst.example.test", "X-CSRF-Token": csrf}


def _cookie_from_response(response: object, name: str) -> str | None:
    values = response.headers.get_list("set-cookie")
    for header_value in values:
        jar = SimpleCookie()
        jar.load(header_value)
        if name in jar:
            return str(jar[name].value)
    return None


def _grant(owner: Principal, state: FabricGrantState) -> FabricGrantRecord:
    now = datetime.now(UTC)
    return FabricGrantRecord(
        id="fabric-grant:semantic_model",
        tenant_id=owner.tenant_id,
        owner_object_id=owner.owner_object_id,
        provider=FabricProvider.SEMANTIC_MODEL,
        fabric_tenant_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        account_hash="f" * 64,
        scope_hash=provider_scope_hash(FabricProvider.SEMANTIC_MODEL),
        audience_hash=audience_hash(),
        state=state,
        cache=CipherEnvelope(
            key_id="https://vault.example/keys/fabric-wrap/1",
            wrapped_dek="AA",
            nonce="AA",
            ciphertext="AA",
        ),
        last_used_at=now,
        expires_at=now + timedelta(minutes=30),
    )


def test_start_requires_csrf_and_strict_body(
    fabric_stack: tuple[TestClient, InMemoryFabricGrantRepository, _FakeTaskService],
) -> None:
    client, _, _ = fabric_stack
    missing_auth = client.post("/api/fabric/auth/start", json={})
    bad_body = client.post(
        "/api/fabric/auth/start",
        json={"taskId": "task_active12345", "provider": "semantic_model"},
        headers=_headers("csrf-a"),
        cookies={"eda_session": "auth_owner_a_1234567890", "eda_csrf": "csrf-a"},
    )

    assert missing_auth.status_code == 401
    assert bad_body.status_code == 422


def test_start_validates_owned_task_and_sets_link_cookie(
    fabric_stack: tuple[TestClient, InMemoryFabricGrantRepository, _FakeTaskService],
) -> None:
    client, _, _ = fabric_stack
    ok = client.post(
        "/api/fabric/auth/start",
        json={"taskId": "task_active12345"},
        headers=_headers("csrf-a"),
        cookies={"eda_session": "auth_owner_a_1234567890", "eda_csrf": "csrf-a"},
    )
    foreign = client.post(
        "/api/fabric/auth/start",
        json={"taskId": "task_foreign123"},
        headers=_headers("csrf-a"),
        cookies={"eda_session": "auth_owner_a_1234567890", "eda_csrf": "csrf-a"},
    )
    terminal = client.post(
        "/api/fabric/auth/start",
        json={"taskId": "task_done123456"},
        headers=_headers("csrf-a"),
        cookies={"eda_session": "auth_owner_a_1234567890", "eda_csrf": "csrf-a"},
    )

    assert ok.status_code == 200
    assert ok.json()["authorizationUrl"].startswith("https://login.microsoftonline.com/")
    set_cookies = ok.headers.get_list("set-cookie")
    assert any("eda_fabric_link=" in value for value in set_cookies)
    assert any("HttpOnly" in value and "Secure" in value and "SameSite=none" in value for value in set_cookies)
    assert any("Max-Age=600" in value and "Path=/api/fabric/auth/callback" in value for value in set_cookies)
    assert foreign.status_code == 404
    assert terminal.status_code == 409


def test_start_allows_configured_fabric_but_rejects_failed_readiness(
    fabric_stack: tuple[TestClient, InMemoryFabricGrantRepository, _FakeTaskService],
) -> None:
    client, _, _ = fabric_stack
    client.app.state.fabric_readiness = FabricPackStatus.CONFIGURED

    configured = client.post(
        "/api/fabric/auth/start",
        json={},
        headers=_headers("csrf-a"),
        cookies={"eda_session": "auth_owner_a_1234567890", "eda_csrf": "csrf-a"},
    )
    client.app.state.fabric_readiness = FabricPackStatus.FAILED
    failed = client.post(
        "/api/fabric/auth/start",
        json={},
        headers=_headers("csrf-a"),
        cookies={"eda_session": "auth_owner_a_1234567890", "eda_csrf": "csrf-a"},
    )

    assert configured.status_code == 200
    assert failed.status_code == 503


def test_callback_requires_link_cookie_and_flow_is_one_time(
    fabric_stack: tuple[TestClient, InMemoryFabricGrantRepository, _FakeTaskService],
) -> None:
    client, _, _ = fabric_stack
    start = client.post(
        "/api/fabric/auth/start",
        json={},
        headers=_headers("csrf-a"),
        cookies={"eda_session": "auth_owner_a_1234567890", "eda_csrf": "csrf-a"},
    )
    assert start.status_code == 200
    missing_cookie = client.post(
        "/api/fabric/auth/callback",
        data={"state": "state_fabric_12345678", "code": "auth-code"},
        cookies={"eda_fabric_link": ""},
    )
    wrong_cookie = client.post(
        "/api/fabric/auth/callback",
        data={"state": "state_fabric_12345678", "code": "auth-code"},
        cookies={"eda_fabric_link": "wrong-cookie"},
    )
    replay = client.post(
        "/api/fabric/auth/callback",
        data={"state": "state_fabric_12345678", "code": "auth-code"},
        cookies={"eda_fabric_link": _cookie_from_response(start, "eda_fabric_link") or ""},
    )

    assert missing_cookie.status_code == 401
    assert wrong_cookie.status_code == 401
    assert replay.status_code == 401


def test_callback_stores_only_pending_and_redirects_without_receipt(
    fabric_stack: tuple[TestClient, InMemoryFabricGrantRepository, _FakeTaskService],
    owners: tuple[Principal, Principal],
) -> None:
    client, repository, _ = fabric_stack
    start = client.post(
        "/api/fabric/auth/start",
        json={"taskId": "task_blocked1234"},
        headers=_headers("csrf-a"),
        cookies={"eda_session": "auth_owner_a_1234567890", "eda_csrf": "csrf-a"},
    )
    link_cookie = _cookie_from_response(start, "eda_fabric_link")
    callback = client.post(
        "/api/fabric/auth/callback",
        data={"state": "state_fabric_12345678", "code": "auth-code"},
        cookies={"eda_fabric_link": link_cookie or ""},
        follow_redirects=False,
    )
    receipt = _cookie_from_response(callback, "eda_fabric_complete")

    assert callback.status_code == 303
    assert callback.headers["location"] == "/fabric-auth/complete"
    assert receipt is not None
    assert receipt not in callback.headers["location"]
    assert receipt not in callback.text
    assert (
        asyncio.run(repository.get_grant(owners[0].tenant_id, owners[0].owner_object_id, FabricProvider.SEMANTIC_MODEL))
        is None
    )
    pending = asyncio.run(
        repository.get_pending_by_receipt(
            owners[0].tenant_id,
            owners[0].owner_object_id,
            FabricProvider.SEMANTIC_MODEL,
            receipt,
        )
    )
    assert pending is not None
    assert pending.task_id == "task_blocked1234"
    assert pending.checkpoint_sequence == 3


def test_complete_requires_session_csrf_and_cookie(
    fabric_stack: tuple[TestClient, InMemoryFabricGrantRepository, _FakeTaskService],
) -> None:
    client, _, _ = fabric_stack
    missing = client.post("/api/fabric/auth/complete")
    missing_cookie = client.post(
        "/api/fabric/auth/complete",
        headers=_headers("csrf-a"),
        cookies={"eda_session": "auth_owner_a_1234567890", "eda_csrf": "csrf-a"},
    )

    assert missing.status_code == 401
    assert missing_cookie.status_code == 401


def test_complete_rejects_owner_mismatch_and_replay(
    fabric_stack: tuple[TestClient, InMemoryFabricGrantRepository, _FakeTaskService],
    owners: tuple[Principal, Principal],
) -> None:
    client, repository, task_service = fabric_stack
    start = client.post(
        "/api/fabric/auth/start",
        json={"taskId": "task_blocked1234"},
        headers=_headers("csrf-a"),
        cookies={"eda_session": "auth_owner_a_1234567890", "eda_csrf": "csrf-a"},
    )
    callback = client.post(
        "/api/fabric/auth/callback",
        data={"state": "state_fabric_12345678", "code": "auth-code"},
        cookies={"eda_fabric_link": _cookie_from_response(start, "eda_fabric_link") or ""},
        follow_redirects=False,
    )
    receipt = _cookie_from_response(callback, "eda_fabric_complete")
    assert receipt is not None

    owner_b = client.post(
        "/api/fabric/auth/complete",
        headers=_headers("csrf-b"),
        cookies={
            "eda_session": "auth_owner_b_1234567890",
            "eda_csrf": "csrf-b",
            "eda_fabric_complete": receipt,
        },
    )
    owner_a = client.post(
        "/api/fabric/auth/complete",
        headers=_headers("csrf-a"),
        cookies={
            "eda_session": "auth_owner_a_1234567890",
            "eda_csrf": "csrf-a",
            "eda_fabric_complete": receipt,
        },
    )
    replay = client.post(
        "/api/fabric/auth/complete",
        headers=_headers("csrf-a"),
        cookies={
            "eda_session": "auth_owner_a_1234567890",
            "eda_csrf": "csrf-a",
            "eda_fabric_complete": receipt,
        },
    )

    assert owner_b.status_code == 401
    assert owner_a.status_code == 204
    assert replay.status_code == 401
    assert task_service.resume_calls == [("task_blocked1234", receipt)]
    grant_a = asyncio.run(
        repository.get_grant(owners[0].tenant_id, owners[0].owner_object_id, FabricProvider.SEMANTIC_MODEL)
    )
    grant_b = asyncio.run(
        repository.get_grant(owners[1].tenant_id, owners[1].owner_object_id, FabricProvider.SEMANTIC_MODEL)
    )
    assert grant_a is not None
    assert grant_b is None
    promoted_cache = asyncio.run(
        EnvelopeCipher(_PassthroughKeyWrapper()).decrypt(
            grant_a.cache,
            EnvelopeContext(
                product_tenant_id=grant_a.tenant_id,
                owner_object_id=grant_a.owner_object_id,
                record_type="fabricGrant",
                record_id=grant_a.id,
            ),
        )
    )
    assert promoted_cache == b"cache-state"
    assert grant_a.expires_at > datetime.now(UTC) + timedelta(days=29)


def test_complete_rejects_stale_checkpoint_before_promotion(
    fabric_stack: tuple[TestClient, InMemoryFabricGrantRepository, _FakeTaskService],
    owners: tuple[Principal, Principal],
) -> None:
    client, repository, task_service = fabric_stack
    start = client.post(
        "/api/fabric/auth/start",
        json={"taskId": "task_blocked1234"},
        headers=_headers("csrf-a"),
        cookies={"eda_session": "auth_owner_a_1234567890", "eda_csrf": "csrf-a"},
    )
    callback = client.post(
        "/api/fabric/auth/callback",
        data={"state": "state_fabric_12345678", "code": "auth-code"},
        cookies={"eda_fabric_link": _cookie_from_response(start, "eda_fabric_link") or ""},
        follow_redirects=False,
    )
    receipt = _cookie_from_response(callback, "eda_fabric_complete")
    assert receipt is not None
    task_service.set_checkpoint("task_blocked1234", 4)

    complete = client.post(
        "/api/fabric/auth/complete",
        headers=_headers("csrf-a"),
        cookies={
            "eda_session": "auth_owner_a_1234567890",
            "eda_csrf": "csrf-a",
            "eda_fabric_complete": receipt,
        },
    )

    assert complete.status_code == 409
    assert (
        asyncio.run(repository.get_grant(owners[0].tenant_id, owners[0].owner_object_id, FabricProvider.SEMANTIC_MODEL))
        is None
    )


def test_status_and_unlink_return_filtered_state_only(
    fabric_stack: tuple[TestClient, InMemoryFabricGrantRepository, _FakeTaskService],
    owners: tuple[Principal, Principal],
) -> None:
    client, repository, _ = fabric_stack
    status_cookies = {"eda_session": "auth_owner_a_1234567890"}
    auth_cookies = {**status_cookies, "eda_csrf": "csrf-a"}
    headers = _headers("csrf-a")

    unlinked = client.get("/api/fabric/auth/status", cookies=status_cookies)
    assert unlinked.status_code == 200
    assert unlinked.json() == {"provider": "semantic_model", "state": "unlinked", "chatQuery": False}

    asyncio.run(repository.create_grant(_grant(owners[0], FabricGrantState.LINKED)))
    linked = client.get("/api/fabric/auth/status", cookies=status_cookies)
    assert linked.json() == {"provider": "semantic_model", "state": "linked", "chatQuery": False}

    existing = asyncio.run(
        repository.get_grant(owners[0].tenant_id, owners[0].owner_object_id, FabricProvider.SEMANTIC_MODEL)
    )
    assert existing is not None and existing.etag is not None
    asyncio.run(
        repository.replace_grant(
            existing.model_copy(update={"state": FabricGrantState.REAUTH_REQUIRED}), etag=existing.etag
        )
    )
    reauth = client.get("/api/fabric/auth/status", cookies=status_cookies)
    assert reauth.json() == {"provider": "semantic_model", "state": "reauth_required", "chatQuery": False}

    deleted = client.delete("/api/fabric/auth", headers=headers, cookies=auth_cookies)
    after_delete = client.get("/api/fabric/auth/status", cookies=status_cookies)
    assert deleted.status_code == 204
    assert after_delete.json() == {"provider": "semantic_model", "state": "unlinked", "chatQuery": False}


def test_status_reports_chat_query_when_the_interactive_source_is_wired(
    fabric_stack: tuple[TestClient, InMemoryFabricGrantRepository, _FakeTaskService],
    owners: tuple[Principal, Principal],
) -> None:
    client, repository, _ = fabric_stack
    status_cookies = {"eda_session": "auth_owner_a_1234567890"}
    asyncio.run(repository.create_grant(_grant(owners[0], FabricGrantState.LINKED)))
    client.app.state.fabric_chat_query = True

    response = client.get("/api/fabric/auth/status", cookies=status_cookies)

    assert response.json() == {"provider": "semantic_model", "state": "linked", "chatQuery": True}


@pytest.mark.parametrize(
    ("alias", "description"),
    [("logistics", "Logistics shipment ontology"), ("energy", "Energy consumption ontology")],
)
def test_status_exposes_only_public_metadata_from_the_configured_ontology(
    fabric_stack: tuple[TestClient, InMemoryFabricGrantRepository, _FakeTaskService],
    settings: Settings,
    alias: str,
    description: str,
) -> None:
    client, _, _ = fabric_stack
    configured = Settings.model_validate(
        {
            **settings.model_dump(),
            "fabric_provider": "ontology",
            "fabric_ontologies": {
                alias: {
                    "workspaceId": str(UUID(int=5)),
                    "ontologyId": str(UUID(int=6)),
                    "graphModelId": str(UUID(int=7)),
                    "description": description,
                }
            },
        }
    )
    client.app.dependency_overrides[settings_dependency] = lambda: configured

    response = client.get("/api/fabric/auth/status", cookies={"eda_session": "auth_owner_a_1234567890"})

    assert response.status_code == 200
    assert response.json() == {
        "provider": "ontology",
        "state": "unlinked",
        "chatQuery": False,
        "source": {"alias": alias, "description": description},
    }
    client.cookies.clear()
    assert client.get("/api/fabric/auth/status").status_code == 401


def test_disabled_fabric_routes_return_not_found(owners: tuple[Principal, Principal]) -> None:
    csrf = "csrf-a"
    settings = Settings.model_validate(
        {
            "public_origin": "https://analyst.example.test",
            "entra_tenant_id": "11111111-1111-1111-1111-111111111111",
            "entra_client_id": "33333333-3333-3333-3333-333333333333",
            "entra_client_secret": "local-only",
            "cosmos_endpoint": "https://enterprise-data-analyst.documents.azure.com",
            "blob_account_url": "https://enterprisedataanalyst.blob.core.windows.net",
            "redis_url": "redis://127.0.0.1:6379/0",
            "foundry_project_endpoint": "https://example.services.ai.azure.com/api/projects/example",
            "foundry_model_deployment": "analysis-opus",
            "fabric_enabled": False,
            "fabric_provider": None,
            "fabric_ontologies": {},
        }
    )
    auth_repository = InMemoryAuthRepository()
    session = AuthSessionRecord.create(
        owners[0],
        csrf_token=csrf,
        ttl_seconds=300,
        id="auth_owner_a_1234567890",
    )
    asyncio.run(auth_repository.put_session(session))
    app = create_app(
        settings_override=settings,
        auth_repository_override=auth_repository,
        msal_override=_NoopMsalClient(),
        workspace_repository_override=InMemoryWorkspaceRepository(),
        upload_service_override=UploadService(
            blob_store=InMemoryBlobStore(),
            upload_repository=InMemoryUploadRepository(),
            upload_limit_bytes=settings.upload_limit_bytes,
        ),
        runtime_repository_override=InMemoryRuntimeStateRepository(),
        event_store_override=NullTaskEventStore(),
        hosted_client_override=_FakeDurableClient(),
    )
    with TestClient(app, base_url="https://analyst.example.test", raise_server_exceptions=False) as client:
        response = client.get(
            "/api/fabric/auth/status",
            headers=_headers(csrf),
            cookies={"eda_session": "auth_owner_a_1234567890", "eda_csrf": csrf},
        )
    assert response.status_code == 404
