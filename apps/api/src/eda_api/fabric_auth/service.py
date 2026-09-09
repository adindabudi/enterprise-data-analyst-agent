from __future__ import annotations

import hashlib
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol, cast
from urllib.parse import urlparse

from eda_contracts.tasks import TaskStatus
from eda_fabric_auth import (
    EnvelopeCipher,
    EnvelopeContext,
    FabricAuthorizationFlowRecord,
    FabricGrantRepository,
    FabricGrantState,
    FabricPendingGrantRecord,
    FabricProvider,
    audience_hash,
    provider_scope_hash,
)
from eda_runtime_state.models import TaskRecord
from fastapi import HTTPException, status

from eda_api.auth.models import Principal
from eda_api.auth.msal_client import AuthenticationError
from eda_api.config import Settings

_TERMINAL_STATUSES = {
    TaskStatus.COMPLETED,
    TaskStatus.CANCELLED,
    TaskStatus.FAILED,
    TaskStatus.FAILED_CANCELLATION,
}


class FabricAuthorizationCodeClient(Protocol):
    async def initiate(self, *, provider: FabricProvider, redirect_uri: str) -> dict[str, Any]: ...

    async def complete(
        self,
        *,
        tenant_id: Any,
        owner_object_id: Any,
        provider: FabricProvider,
        flow: dict[str, Any],
        auth_response: dict[str, str],
    ) -> FabricPendingGrantRecord: ...


class TaskServiceProtocol(Protocol):
    async def get_owned_task(self, principal: Principal, task_id: str) -> TaskRecord | None: ...

    async def resume_auth(self, partition: Any, task_id: str, receipt: str) -> Any: ...


class FabricAuthStartResult(Protocol):
    @property
    def authorization_url(self) -> str: ...

    @property
    def correlation_secret(self) -> str: ...


class FabricAuthCoordinator:
    def __init__(
        self,
        *,
        settings: Settings,
        provider: FabricProvider,
        client: FabricAuthorizationCodeClient,
        repository: FabricGrantRepository,
        cipher: EnvelopeCipher,
        task_service: TaskServiceProtocol,
    ) -> None:
        self._settings = settings
        self._provider = provider
        self._client = client
        self._repository = repository
        self._cipher = cipher
        self._task_service = task_service

    async def start(
        self,
        *,
        principal: Principal,
        task_id: str | None,
    ) -> tuple[str, str]:
        if self._settings.app_env == "production" and self._settings.public_origin.scheme != "https":
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)

        bound_task_id: str | None = None
        bound_checkpoint: int | None = None
        if task_id is not None:
            task = await self._task_service.get_owned_task(principal, task_id)
            if task is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
            if task.status in _TERMINAL_STATUSES:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT)
            bound_task_id = task.id
            bound_checkpoint = task.checkpoint_sequence

        flow = await self._client.initiate(provider=self._provider, redirect_uri=self._callback_url())
        state_value = flow.get("state")
        auth_uri = flow.get("auth_uri")
        if not isinstance(state_value, str) or not isinstance(auth_uri, str):
            raise AuthenticationError("Fabric authorization is required.")
        self._validate_authorization_url(auth_uri)

        correlation_secret = secrets.token_urlsafe(32)
        flow_id = state_value
        record_id = FabricAuthorizationFlowRecord.make_id(self._provider, flow_id)
        context = EnvelopeContext(
            product_tenant_id=principal.tenant_id,
            owner_object_id=principal.owner_object_id,
            record_type="fabricAuthorizationFlow",
            record_id=record_id,
        )
        encrypted_flow = await self._cipher.encrypt(_canonical_json(flow), context)
        flow_record = FabricAuthorizationFlowRecord(
            id=record_id,
            flow_id=flow_id,
            tenant_id=principal.tenant_id,
            owner_object_id=principal.owner_object_id,
            provider=self._provider,
            scope_hash=provider_scope_hash(self._provider),
            audience_hash=audience_hash(),
            correlation_secret_hash=_sha256_text(correlation_secret),
            flow=encrypted_flow,
            task_id=bound_task_id,
            checkpoint_sequence=bound_checkpoint,
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
        )
        await self._repository.put_flow(flow_record)
        return auth_uri, correlation_secret

    async def callback(
        self,
        *,
        state_value: str,
        correlation_secret: str,
        code: str | None,
        error: str | None,
    ) -> str:
        if not correlation_secret:
            raise AuthenticationError("Fabric authorization is required.")

        flow_record = await self._repository.pop_flow(self._provider, state_value)
        if flow_record is None:
            raise AuthenticationError("Fabric authorization is required.")

        expected_hash = flow_record.correlation_secret_hash
        actual_hash = _sha256_text(correlation_secret)
        if not secrets.compare_digest(actual_hash, expected_hash):
            raise AuthenticationError("Fabric authorization is required.")

        if error is not None:
            raise AuthenticationError("Fabric authorization is required.")
        if not code:
            raise AuthenticationError("Fabric authorization is required.")

        context = EnvelopeContext(
            product_tenant_id=flow_record.tenant_id,
            owner_object_id=flow_record.owner_object_id,
            record_type="fabricAuthorizationFlow",
            record_id=flow_record.id,
        )
        flow = _parse_flow(await self._cipher.decrypt(flow_record.flow, context))

        pending = await self._client.complete(
            tenant_id=flow_record.tenant_id,
            owner_object_id=flow_record.owner_object_id,
            provider=self._provider,
            flow=flow,
            auth_response={"state": state_value, "code": code},
        )
        if flow_record.task_id is not None or flow_record.checkpoint_sequence is not None:
            pending = pending.model_copy(
                update={
                    "task_id": flow_record.task_id,
                    "checkpoint_sequence": flow_record.checkpoint_sequence,
                }
            )
        await self._repository.put_pending(pending)
        return pending.receipt

    async def complete(self, *, principal: Principal, receipt: str) -> None:
        pending = await self._repository.get_pending_by_receipt(
            principal.tenant_id,
            principal.owner_object_id,
            self._provider,
            receipt,
        )
        if pending is None:
            raise AuthenticationError("Fabric authorization is required.")

        task: TaskRecord | None = None
        if pending.task_id is not None:
            task = await self._task_service.get_owned_task(principal, pending.task_id)
            if task is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
            if task.status is not TaskStatus.BLOCKED_AUTH:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT)
            if pending.checkpoint_sequence is None or task.checkpoint_sequence != pending.checkpoint_sequence:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT)

        pending_context = EnvelopeContext(
            product_tenant_id=pending.tenant_id,
            owner_object_id=pending.owner_object_id,
            record_type="fabricPendingGrant",
            record_id=pending.id,
        )
        grant_id = f"fabric-grant:{self._provider.value}"
        grant_context = EnvelopeContext(
            product_tenant_id=pending.tenant_id,
            owner_object_id=pending.owner_object_id,
            record_type="fabricGrant",
            record_id=grant_id,
        )
        serialized_cache = await self._cipher.decrypt(pending.cache, pending_context)
        promoted_cache = await self._cipher.encrypt(serialized_cache, grant_context)

        promoted = await self._repository.promote_pending(
            principal.tenant_id,
            principal.owner_object_id,
            self._provider,
            receipt,
            expected_scope_hash=provider_scope_hash(self._provider),
            expected_audience_hash=audience_hash(),
            promoted_cache=promoted_cache,
        )
        if promoted is None:
            raise AuthenticationError("Fabric authorization is required.")

        if task is not None:
            await self._task_service.resume_auth(task.partition(), task.id, receipt)

    async def status(self, *, principal: Principal) -> Literal["unlinked", "linked", "reauth_required"]:
        grant = await self._repository.get_grant(
            principal.tenant_id,
            principal.owner_object_id,
            self._provider,
        )
        if grant is None:
            return "unlinked"
        if grant.state is FabricGrantState.REAUTH_REQUIRED:
            return "reauth_required"
        return "linked"

    async def unlink(self, *, principal: Principal) -> None:
        await self._repository.delete_grant(principal.tenant_id, principal.owner_object_id, self._provider)

    def _callback_url(self) -> str:
        return f"{str(self._settings.public_origin).rstrip('/')}/api/fabric/auth/callback"

    def _validate_authorization_url(self, authorization_url: str) -> None:
        parsed = urlparse(authorization_url)
        tenant = str(self._settings.fabric_tenant_id)
        if parsed.scheme != "https" or parsed.hostname != "login.microsoftonline.com":
            raise AuthenticationError("Fabric authorization is required.")
        path_parts = [part for part in parsed.path.split("/") if part]
        if not path_parts or path_parts[0].lower() != tenant.lower():
            raise AuthenticationError("Fabric authorization is required.")


def _canonical_json(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _parse_flow(payload: bytes) -> dict[str, Any]:
    raw = json.loads(payload.decode("utf-8"))
    if not isinstance(raw, dict):
        raise AuthenticationError("Fabric authorization is required.")
    raw_mapping = cast(dict[object, Any], raw)
    if not all(isinstance(key, str) for key in raw_mapping):
        raise AuthenticationError("Fabric authorization is required.")
    return {cast(str, key): value for key, value in raw_mapping.items()}


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
