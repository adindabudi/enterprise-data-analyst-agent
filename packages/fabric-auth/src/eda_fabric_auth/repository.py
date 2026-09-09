from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import UUID

from azure.core import MatchConditions
from azure.cosmos.exceptions import CosmosHttpResponseError

from .crypto import CipherEnvelope
from .models import (
    FabricAuthorizationFlowRecord,
    FabricGrantRecord,
    FabricGrantState,
    FabricPendingGrantRecord,
    FabricProvider,
)


def _clean_document(document: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value for key, value in document.items() if key != "ttl" and (key == "_etag" or not key.startswith("_"))
    }


class FabricGrantConflict(RuntimeError):
    pass


class CosmosContainer(Protocol):
    async def create_item(self, body: dict[str, Any], **kwargs: Any) -> dict[str, Any]: ...

    async def read_item(self, item: str, partition_key: Any, **kwargs: Any) -> dict[str, Any]: ...

    async def replace_item(
        self,
        item: str,
        body: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]: ...

    async def delete_item(self, item: str, partition_key: Any, **kwargs: Any) -> None: ...


class FabricGrantRepository(Protocol):
    async def put_flow(self, flow: FabricAuthorizationFlowRecord) -> None: ...

    async def pop_flow(self, provider: FabricProvider, flow_id: str) -> FabricAuthorizationFlowRecord | None: ...

    async def put_pending(self, pending_grant: FabricPendingGrantRecord) -> None: ...

    async def get_pending_by_receipt(
        self,
        tenant_id: UUID,
        owner_object_id: UUID,
        provider: FabricProvider,
        receipt: str,
    ) -> FabricPendingGrantRecord | None: ...

    async def promote_pending(
        self,
        tenant_id: UUID,
        owner_object_id: UUID,
        provider: FabricProvider,
        receipt: str,
        expected_scope_hash: str,
        expected_audience_hash: str,
        promoted_cache: CipherEnvelope,
    ) -> FabricGrantRecord | None: ...

    async def get_grant(
        self,
        tenant_id: UUID,
        owner_object_id: UUID,
        provider: FabricProvider,
    ) -> FabricGrantRecord | None: ...

    async def create_grant(self, grant: FabricGrantRecord) -> FabricGrantRecord: ...

    async def replace_grant(self, grant: FabricGrantRecord, *, etag: str) -> FabricGrantRecord: ...

    async def delete_grant(self, tenant_id: UUID, owner_object_id: UUID, provider: FabricProvider) -> None: ...


class InMemoryFabricGrantRepository:
    def __init__(self) -> None:
        self._grants: dict[tuple[UUID, UUID, FabricProvider], FabricGrantRecord] = {}
        self._flows: dict[tuple[FabricProvider, str], FabricAuthorizationFlowRecord] = {}
        self._pending_grants: dict[tuple[UUID, UUID, FabricProvider, str], FabricPendingGrantRecord] = {}
        self._etag_counter = 0

    def _next_etag(self) -> str:
        self._etag_counter += 1
        return f'W/"{self._etag_counter}"'

    def _with_etag(self, record: FabricGrantRecord) -> FabricGrantRecord:
        return record.model_copy(update={"etag": self._next_etag()})

    def _with_flow_etag(self, record: FabricAuthorizationFlowRecord) -> FabricAuthorizationFlowRecord:
        return record.model_copy(update={"etag": self._next_etag()})

    def _with_pending_etag(self, record: FabricPendingGrantRecord) -> FabricPendingGrantRecord:
        return record.model_copy(update={"etag": self._next_etag()})

    async def put_grant(self, grant: FabricGrantRecord) -> FabricGrantRecord:
        return await self.create_grant(grant)

    async def put(self, grant: FabricGrantRecord) -> None:
        await self.put_grant(grant)

    async def create_grant(self, grant: FabricGrantRecord) -> FabricGrantRecord:
        key = (grant.tenant_id, grant.owner_object_id, grant.provider)
        existing = self._grants.get(key)
        if existing is not None and existing.expires_at > datetime.now(UTC):
            raise FabricGrantConflict("grant already exists")
        stored = self._with_etag(grant)
        self._grants[key] = stored
        return stored

    async def replace_grant(self, grant: FabricGrantRecord, *, etag: str) -> FabricGrantRecord:
        key = (grant.tenant_id, grant.owner_object_id, grant.provider)
        existing = self._grants.get(key)
        if existing is None or existing.expires_at <= datetime.now(UTC):
            self._grants.pop(key, None)
            raise FabricGrantConflict("grant does not exist")
        if existing.etag != etag:
            raise FabricGrantConflict("grant ETag mismatch")
        stored = self._with_etag(grant)
        self._grants[key] = stored
        return stored

    async def get_grant(
        self,
        tenant_id: UUID,
        owner_object_id: UUID,
        provider: FabricProvider,
    ) -> FabricGrantRecord | None:
        key = (tenant_id, owner_object_id, provider)
        grant = self._grants.get(key)
        if grant is None:
            return None
        if grant.expires_at <= datetime.now(UTC):
            del self._grants[key]
            return None
        return grant

    async def get(
        self,
        tenant_id: UUID,
        owner_object_id: UUID,
        provider: FabricProvider,
    ) -> FabricGrantRecord | None:
        return await self.get_grant(tenant_id, owner_object_id, provider)

    async def delete_grant(self, tenant_id: UUID, owner_object_id: UUID, provider: FabricProvider) -> None:
        self._grants.pop((tenant_id, owner_object_id, provider), None)

    async def delete(self, tenant_id: UUID, owner_object_id: UUID, provider: FabricProvider) -> None:
        await self.delete_grant(tenant_id, owner_object_id, provider)

    async def put_flow(self, flow: FabricAuthorizationFlowRecord) -> None:
        key = (flow.provider, flow.flow_id)
        if key in self._flows:
            raise ValueError("flow ID already exists")
        self._flows[key] = self._with_flow_etag(flow)

    async def pop_flow(self, provider: FabricProvider, flow_id: str) -> FabricAuthorizationFlowRecord | None:
        key = (provider, flow_id)
        flow = self._flows.pop(key, None)
        if flow is None or flow.expires_at <= datetime.now(UTC):
            return None
        return flow

    async def put_pending(self, pending_grant: FabricPendingGrantRecord) -> None:
        key = (pending_grant.tenant_id, pending_grant.owner_object_id, pending_grant.provider, pending_grant.receipt)
        if key in self._pending_grants:
            raise ValueError("pending receipt already exists")
        self._pending_grants[key] = self._with_pending_etag(pending_grant)

    async def get_pending_by_receipt(
        self,
        tenant_id: UUID,
        owner_object_id: UUID,
        provider: FabricProvider,
        receipt: str,
    ) -> FabricPendingGrantRecord | None:
        key = (tenant_id, owner_object_id, provider, receipt)
        pending = self._pending_grants.get(key)
        if pending is None:
            return None
        if pending.expires_at <= datetime.now(UTC):
            del self._pending_grants[key]
            return None
        return pending

    async def promote_pending(
        self,
        tenant_id: UUID,
        owner_object_id: UUID,
        provider: FabricProvider,
        receipt: str,
        expected_scope_hash: str,
        expected_audience_hash: str,
        promoted_cache: CipherEnvelope,
    ) -> FabricGrantRecord | None:
        key = (tenant_id, owner_object_id, provider, receipt)
        pending_grant = self._pending_grants.get(key)
        if pending_grant is None:
            return None
        if pending_grant.expires_at <= datetime.now(UTC):
            del self._pending_grants[key]
            return None
        if pending_grant.scope_hash != expected_scope_hash or pending_grant.audience_hash != expected_audience_hash:
            return None

        existing = await self.get_grant(tenant_id, owner_object_id, provider)
        if existing is not None:
            if (
                existing.fabric_tenant_id != pending_grant.fabric_tenant_id
                or existing.account_hash != pending_grant.account_hash
            ):
                raise FabricGrantConflict("grant already exists with different binding")
            if existing.scope_hash == expected_scope_hash and existing.audience_hash == expected_audience_hash:
                del self._pending_grants[key]
                return existing
            if existing.etag is None:
                raise FabricGrantConflict("grant is missing an ETag")
            # The same account just re-consented, so the fresh scopes supersede the stored grant.
            moment = datetime.now(UTC)
            renewed = existing.model_copy(
                update={
                    "scope_hash": pending_grant.scope_hash,
                    "audience_hash": pending_grant.audience_hash,
                    "state": FabricGrantState.LINKED,
                    "cache": promoted_cache,
                    "last_used_at": moment,
                    "expires_at": moment + timedelta(days=30),
                }
            )
            del self._pending_grants[key]
            return await self.replace_grant(renewed, etag=existing.etag)

        now = datetime.now(UTC)
        grant = FabricGrantRecord(
            id=f"fabric-grant:{provider.value}",
            tenant_id=tenant_id,
            owner_object_id=owner_object_id,
            provider=provider,
            fabric_tenant_id=pending_grant.fabric_tenant_id,
            account_hash=pending_grant.account_hash,
            scope_hash=pending_grant.scope_hash,
            audience_hash=pending_grant.audience_hash,
            state=FabricGrantState.LINKED,
            cache=promoted_cache,
            last_used_at=now,
            expires_at=now + timedelta(days=30),
        )
        del self._pending_grants[key]
        return await self.create_grant(grant)

    async def get_pending(
        self,
        tenant_id: UUID,
        owner_object_id: UUID,
        provider: FabricProvider,
        scope_hash: str,
        audience_hash: str,
    ) -> FabricPendingGrantRecord | None:
        for receipt_key in list(self._pending_grants):
            current_tenant, current_owner, current_provider, _receipt = receipt_key
            if current_tenant != tenant_id or current_owner != owner_object_id or current_provider is not provider:
                continue
            pending_grant = self._pending_grants[receipt_key]
            if pending_grant.expires_at <= datetime.now(UTC):
                del self._pending_grants[receipt_key]
                continue
            if pending_grant.scope_hash == scope_hash and pending_grant.audience_hash == audience_hash:
                return pending_grant
        return None


class CosmosFabricGrantRepository:
    def __init__(self, auth_container: CosmosContainer, fabric_container: CosmosContainer) -> None:
        self._auth_container = auth_container
        self._fabric_container = fabric_container

    async def put_grant(self, grant: FabricGrantRecord) -> FabricGrantRecord:
        return await self.create_grant(grant)

    async def create_grant(self, grant: FabricGrantRecord) -> FabricGrantRecord:
        try:
            document = await self._fabric_container.create_item(grant.to_document())
            return FabricGrantRecord.model_validate(_clean_document(document))
        except CosmosHttpResponseError as exc:
            if _is_status(exc, 409, 412):
                raise FabricGrantConflict("grant already exists") from exc
            raise

    async def replace_grant(self, grant: FabricGrantRecord, *, etag: str) -> FabricGrantRecord:
        try:
            document = await self._fabric_container.replace_item(
                item=grant.id,
                body=grant.to_document(),
                etag=etag,
                match_condition=MatchConditions.IfNotModified,
            )
            return FabricGrantRecord.model_validate(_clean_document(document))
        except CosmosHttpResponseError as exc:
            if _is_status(exc, 409, 412):
                raise FabricGrantConflict("grant ETag mismatch") from exc
            if _is_status(exc, 404):
                raise FabricGrantConflict("grant does not exist") from exc
            raise

    async def get_grant(
        self,
        tenant_id: UUID,
        owner_object_id: UUID,
        provider: FabricProvider,
    ) -> FabricGrantRecord | None:
        item_id = f"fabric-grant:{provider.value}"
        partition_key = [str(tenant_id), str(owner_object_id)]
        try:
            document = await self._fabric_container.read_item(item=item_id, partition_key=partition_key)
        except CosmosHttpResponseError as exc:
            if _is_status(exc, 404):
                return None
            raise

        grant = FabricGrantRecord.model_validate(_clean_document(document))
        if grant.expires_at <= datetime.now(UTC):
            await self.delete_grant(tenant_id, owner_object_id, provider)
            return None
        return grant

    async def delete_grant(self, tenant_id: UUID, owner_object_id: UUID, provider: FabricProvider) -> None:
        item_id = f"fabric-grant:{provider.value}"
        partition_key = [str(tenant_id), str(owner_object_id)]
        try:
            await self._fabric_container.delete_item(item=item_id, partition_key=partition_key)
        except CosmosHttpResponseError as exc:
            if _is_status(exc, 404):
                return
            raise

    async def put(self, grant: FabricGrantRecord) -> None:
        await self.put_grant(grant)

    async def get(
        self,
        tenant_id: UUID,
        owner_object_id: UUID,
        provider: FabricProvider,
    ) -> FabricGrantRecord | None:
        return await self.get_grant(tenant_id, owner_object_id, provider)

    async def delete(self, tenant_id: UUID, owner_object_id: UUID, provider: FabricProvider) -> None:
        await self.delete_grant(tenant_id, owner_object_id, provider)

    async def put_flow(self, flow: FabricAuthorizationFlowRecord) -> None:
        try:
            await self._auth_container.create_item(flow.to_document())
        except CosmosHttpResponseError as exc:
            if _is_status(exc, 409, 412):
                raise FabricGrantConflict("flow already exists") from exc
            raise

    async def pop_flow(self, provider: FabricProvider, flow_id: str) -> FabricAuthorizationFlowRecord | None:
        item_id = FabricAuthorizationFlowRecord.make_id(provider, flow_id)
        try:
            document = await self._auth_container.read_item(item=item_id, partition_key=item_id)
        except CosmosHttpResponseError as exc:
            if _is_status(exc, 404):
                return None
            raise

        flow = FabricAuthorizationFlowRecord.model_validate(_clean_document(document))
        if flow.expires_at <= datetime.now(UTC):
            await self._delete_auth_item(item_id=item_id, etag=flow.etag)
            return None

        deleted = await self._delete_auth_item(item_id=item_id, etag=flow.etag)
        if not deleted:
            return None
        return flow

    async def put_pending(self, pending_grant: FabricPendingGrantRecord) -> None:
        try:
            await self._auth_container.create_item(pending_grant.to_document())
        except CosmosHttpResponseError as exc:
            if _is_status(exc, 409, 412):
                raise FabricGrantConflict("pending receipt already exists") from exc
            raise

    async def get_pending_by_receipt(
        self,
        tenant_id: UUID,
        owner_object_id: UUID,
        provider: FabricProvider,
        receipt: str,
    ) -> FabricPendingGrantRecord | None:
        item_id = FabricPendingGrantRecord.make_id(provider, receipt)
        try:
            pending_document = await self._auth_container.read_item(item=item_id, partition_key=item_id)
        except CosmosHttpResponseError as exc:
            if _is_status(exc, 404):
                return None
            raise

        pending = FabricPendingGrantRecord.model_validate(_clean_document(pending_document))
        if (
            pending.tenant_id != tenant_id
            or pending.owner_object_id != owner_object_id
            or pending.provider is not provider
        ):
            return None
        if pending.expires_at <= datetime.now(UTC):
            await self._delete_auth_item(item_id=item_id, etag=pending.etag)
            return None
        return pending

    async def promote_pending(
        self,
        tenant_id: UUID,
        owner_object_id: UUID,
        provider: FabricProvider,
        receipt: str,
        expected_scope_hash: str,
        expected_audience_hash: str,
        promoted_cache: CipherEnvelope,
    ) -> FabricGrantRecord | None:
        item_id = FabricPendingGrantRecord.make_id(provider, receipt)
        try:
            pending_document = await self._auth_container.read_item(item=item_id, partition_key=item_id)
        except CosmosHttpResponseError as exc:
            if _is_status(exc, 404):
                return None
            raise

        pending = FabricPendingGrantRecord.model_validate(_clean_document(pending_document))
        if (
            pending.tenant_id != tenant_id
            or pending.owner_object_id != owner_object_id
            or pending.provider is not provider
        ):
            return None
        if pending.expires_at <= datetime.now(UTC):
            await self._delete_auth_item(item_id=item_id, etag=pending.etag)
            return None
        if pending.scope_hash != expected_scope_hash or pending.audience_hash != expected_audience_hash:
            return None

        existing_grant = await self.get_grant(tenant_id, owner_object_id, provider)
        if existing_grant is not None:
            if (
                existing_grant.fabric_tenant_id != pending.fabric_tenant_id
                or existing_grant.account_hash != pending.account_hash
            ):
                raise FabricGrantConflict("grant already exists with different binding")
            if (
                existing_grant.scope_hash == expected_scope_hash
                and existing_grant.audience_hash == expected_audience_hash
            ):
                await self._delete_auth_item(item_id=item_id, etag=pending.etag)
                return existing_grant
            if existing_grant.etag is None:
                raise FabricGrantConflict("grant is missing an ETag")
            # The same account just re-consented, so the fresh scopes supersede the stored grant.
            moment = datetime.now(UTC)
            renewed = existing_grant.model_copy(
                update={
                    "scope_hash": pending.scope_hash,
                    "audience_hash": pending.audience_hash,
                    "state": FabricGrantState.LINKED,
                    "cache": promoted_cache,
                    "last_used_at": moment,
                    "expires_at": moment + timedelta(days=30),
                }
            )
            await self._delete_auth_item(item_id=item_id, etag=pending.etag)
            return await self.replace_grant(renewed, etag=existing_grant.etag)

        deleted = await self._delete_auth_item(item_id=item_id, etag=pending.etag)
        if not deleted:
            existing_after_delete = await self.get_grant(tenant_id, owner_object_id, provider)
            if existing_after_delete is not None and existing_after_delete.scope_hash == expected_scope_hash:
                if existing_after_delete.audience_hash == expected_audience_hash:
                    return existing_after_delete
            return None

        now = datetime.now(UTC)
        grant = FabricGrantRecord(
            id=f"fabric-grant:{provider.value}",
            tenant_id=tenant_id,
            owner_object_id=owner_object_id,
            provider=provider,
            fabric_tenant_id=pending.fabric_tenant_id,
            account_hash=pending.account_hash,
            scope_hash=pending.scope_hash,
            audience_hash=pending.audience_hash,
            state=FabricGrantState.LINKED,
            cache=promoted_cache,
            last_used_at=now,
            expires_at=now + timedelta(days=30),
        )
        try:
            return await self.create_grant(grant)
        except FabricGrantConflict:
            existing_after_create_conflict = await self.get_grant(tenant_id, owner_object_id, provider)
            if (
                existing_after_create_conflict is not None
                and existing_after_create_conflict.scope_hash == expected_scope_hash
            ):
                if existing_after_create_conflict.audience_hash == expected_audience_hash:
                    return existing_after_create_conflict
            raise

    async def _delete_auth_item(self, *, item_id: str, etag: str | None) -> bool:
        try:
            await self._auth_container.delete_item(
                item=item_id,
                partition_key=item_id,
                etag=etag,
                match_condition=MatchConditions.IfNotModified,
            )
            return True
        except CosmosHttpResponseError as exc:
            if _is_status(exc, 404, 412):
                return False
            raise


def _is_status(error: CosmosHttpResponseError, *status_codes: int) -> bool:
    return bool(getattr(error, "status_code", None) in status_codes)
