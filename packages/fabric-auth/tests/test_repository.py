from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from azure.core import MatchConditions
from azure.cosmos.exceptions import CosmosHttpResponseError
from eda_fabric_auth.crypto import CipherEnvelope
from eda_fabric_auth.models import (
    FabricAuthorizationFlowRecord,
    FabricGrantRecord,
    FabricGrantState,
    FabricPendingGrantRecord,
    FabricProvider,
)
from eda_fabric_auth.repository import CosmosFabricGrantRepository, FabricGrantConflict, InMemoryFabricGrantRepository
from pydantic import ValidationError


def _envelope() -> CipherEnvelope:
    return CipherEnvelope(
        key_id="https://vault.example/keys/fabric/v1",
        wrapped_dek="AA",
        nonce="AA",
        ciphertext="AA",
    )


def _grant(provider: FabricProvider = FabricProvider.ONTOLOGY) -> FabricGrantRecord:
    return FabricGrantRecord(
        id=f"fabric-grant:{provider.value}",
        tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
        owner_object_id=UUID("22222222-2222-2222-2222-222222222222"),
        provider=provider,
        fabric_tenant_id=UUID("33333333-3333-3333-3333-333333333333"),
        account_hash="d" * 64,
        scope_hash="a" * 64,
        audience_hash="b" * 64,
        state=FabricGrantState.LINKED,
        cache=_envelope(),
        last_used_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )


def _flow(provider: FabricProvider = FabricProvider.ONTOLOGY) -> FabricAuthorizationFlowRecord:
    flow_id = "state_abcdefgh"
    return FabricAuthorizationFlowRecord(
        id=FabricAuthorizationFlowRecord.make_id(provider, flow_id),
        flow_id=flow_id,
        tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
        owner_object_id=UUID("22222222-2222-2222-2222-222222222222"),
        provider=provider,
        scope_hash="a" * 64,
        audience_hash="b" * 64,
        correlation_secret_hash="e" * 64,
        flow=_envelope(),
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )


def _pending(provider: FabricProvider = FabricProvider.ONTOLOGY) -> FabricPendingGrantRecord:
    receipt = "receipt_abcdefgh"
    return FabricPendingGrantRecord(
        id=FabricPendingGrantRecord.make_id(provider, receipt),
        receipt=receipt,
        tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
        owner_object_id=UUID("22222222-2222-2222-2222-222222222222"),
        provider=provider,
        scope_hash="a" * 64,
        audience_hash="b" * 64,
        fabric_tenant_id=UUID("33333333-3333-3333-3333-333333333333"),
        account_hash="d" * 64,
        cache=_envelope(),
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )


class FakeCosmosError(CosmosHttpResponseError):
    def __init__(self, status_code: int) -> None:
        super().__init__(message=f"status={status_code}")
        self.status_code = status_code


class FakeContainer:
    def __init__(self) -> None:
        self.items: dict[tuple[str, tuple[str, ...]], dict[str, Any]] = {}
        self.last_read_partition_key: Any = None
        self.last_replace_match_condition: MatchConditions | None = None
        self.last_delete_match_condition: MatchConditions | None = None
        self.conflict_on_create = False
        self.conflict_on_replace = False
        self.precondition_on_replace = False

    @staticmethod
    def _normalize_partition_key(partition_key: Any) -> tuple[str, ...]:
        if isinstance(partition_key, list):
            return tuple(str(value) for value in partition_key)
        return (str(partition_key),)

    async def create_item(self, body: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        del kwargs
        if self.conflict_on_create:
            raise FakeCosmosError(409)
        key = (str(body["id"]), self._normalize_partition_key(_partition_from_body(body)))
        if key in self.items:
            raise FakeCosmosError(409)
        stored = {**body, "_etag": f'W/"{len(self.items) + 1}"'}
        self.items[key] = stored
        return stored

    async def read_item(self, item: str, partition_key: Any, **kwargs: Any) -> dict[str, Any]:
        del kwargs
        self.last_read_partition_key = partition_key
        key = (item, self._normalize_partition_key(partition_key))
        if key not in self.items:
            raise FakeCosmosError(404)
        return self.items[key]

    async def replace_item(self, item: str, body: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        self.last_replace_match_condition = kwargs.get("match_condition")
        if self.conflict_on_replace:
            raise FakeCosmosError(409)
        if self.precondition_on_replace:
            raise FakeCosmosError(412)
        key = (item, self._normalize_partition_key(_partition_from_body(body)))
        if key not in self.items:
            raise FakeCosmosError(404)
        etag = kwargs.get("etag")
        if etag is not None and self.items[key].get("_etag") != etag:
            raise FakeCosmosError(412)
        stored = {**body, "_etag": f'W/"{len(self.items) + 1}"'}
        self.items[key] = stored
        return stored

    async def delete_item(self, item: str, partition_key: Any, **kwargs: Any) -> None:
        self.last_delete_match_condition = kwargs.get("match_condition")
        key = (item, self._normalize_partition_key(partition_key))
        if key not in self.items:
            raise FakeCosmosError(404)
        etag = kwargs.get("etag")
        if etag is not None and self.items[key].get("_etag") != etag:
            raise FakeCosmosError(412)
        del self.items[key]


def _partition_from_body(body: dict[str, Any]) -> Any:
    if str(body.get("id", "")).startswith("fabric-grant:"):
        return [body["tenantId"], body["ownerObjectId"]]
    return body["id"]


def test_model_documents_use_camel_case_and_positive_ttl() -> None:
    now = datetime.now(UTC)
    grant_doc = _grant().to_document(now=now)
    flow_doc = _flow().to_document(now=now)
    pending_doc = _pending().to_document(now=now)

    assert grant_doc["ttl"] >= 1
    assert flow_doc["ttl"] >= 1
    assert pending_doc["ttl"] >= 1

    assert "tenantId" in grant_doc
    assert "ownerObjectId" in grant_doc
    assert "fabricTenantId" in grant_doc
    assert "lastUsedAt" in grant_doc
    assert "cache" in grant_doc
    assert "keyId" in grant_doc["cache"]
    assert "wrappedDek" in grant_doc["cache"]

    assert "tenant_id" not in grant_doc
    assert "owner_object_id" not in grant_doc
    assert "fabric_tenant_id" not in grant_doc
    assert "last_used_at" not in grant_doc
    assert "_etag" not in grant_doc


@pytest.mark.asyncio
async def test_cosmos_uses_full_hpk_for_grant_point_read() -> None:
    auth_container = FakeContainer()
    fabric_container = FakeContainer()
    repository = CosmosFabricGrantRepository(auth_container, fabric_container)

    grant = await repository.create_grant(_grant())
    fetched = await repository.get_grant(grant.tenant_id, grant.owner_object_id, grant.provider)

    assert fetched is not None
    assert fabric_container.last_read_partition_key == [str(grant.tenant_id), str(grant.owner_object_id)]


@pytest.mark.asyncio
async def test_cosmos_pop_flow_deletes_once_with_if_not_modified() -> None:
    auth_container = FakeContainer()
    fabric_container = FakeContainer()
    repository = CosmosFabricGrantRepository(auth_container, fabric_container)

    flow = _flow()
    await repository.put_flow(flow)
    stored = next(iter(auth_container.items.values()))
    stored.update(
        {
            "_rid": "opaque-resource-id",
            "_self": "dbs/opaque/colls/opaque/docs/opaque/",
            "_attachments": "attachments/",
            "_ts": 1785235533,
        }
    )
    first = await repository.pop_flow(flow.provider, flow.flow_id)
    second = await repository.pop_flow(flow.provider, flow.flow_id)

    assert first is not None
    assert second is None
    assert auth_container.last_delete_match_condition == MatchConditions.IfNotModified


@pytest.mark.asyncio
async def test_cosmos_replace_grant_requires_if_not_modified() -> None:
    auth_container = FakeContainer()
    fabric_container = FakeContainer()
    repository = CosmosFabricGrantRepository(auth_container, fabric_container)

    created = await repository.create_grant(_grant())
    assert created.etag is not None

    replacement = created.model_copy(update={"last_used_at": created.last_used_at + timedelta(minutes=1)})
    await repository.replace_grant(replacement, etag=created.etag)

    assert fabric_container.last_replace_match_condition == MatchConditions.IfNotModified


@pytest.mark.asyncio
async def test_cosmos_conflicts_map_to_fabric_grant_conflict() -> None:
    auth_container = FakeContainer()
    fabric_container = FakeContainer()
    repository = CosmosFabricGrantRepository(auth_container, fabric_container)

    fabric_container.conflict_on_create = True
    with pytest.raises(FabricGrantConflict):
        await repository.create_grant(_grant())

    fabric_container.conflict_on_create = False
    created = await repository.create_grant(_grant())
    assert created.etag is not None

    fabric_container.precondition_on_replace = True
    with pytest.raises(FabricGrantConflict):
        await repository.replace_grant(created, etag=created.etag)


@pytest.mark.asyncio
async def test_promote_pending_rejects_owner_mismatch() -> None:
    repository = InMemoryFabricGrantRepository()
    pending = _pending()
    await repository.put_pending(pending)

    promoted = await repository.promote_pending(
        tenant_id=pending.tenant_id,
        owner_object_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        provider=pending.provider,
        receipt=pending.receipt,
        expected_scope_hash=pending.scope_hash,
        expected_audience_hash=pending.audience_hash,
        promoted_cache=pending.cache,
    )

    assert promoted is None


@pytest.mark.asyncio
async def test_promote_pending_renews_a_grant_whose_scopes_changed() -> None:
    repository = InMemoryFabricGrantRepository()
    stored = await repository.create_grant(_grant().model_copy(update={"scope_hash": "c" * 64}))
    pending = _pending()
    await repository.put_pending(pending)

    promoted = await repository.promote_pending(
        tenant_id=pending.tenant_id,
        owner_object_id=pending.owner_object_id,
        provider=pending.provider,
        receipt=pending.receipt,
        expected_scope_hash=pending.scope_hash,
        expected_audience_hash=pending.audience_hash,
        promoted_cache=pending.cache,
    )

    assert promoted is not None
    assert promoted.scope_hash == pending.scope_hash
    assert promoted.state is FabricGrantState.LINKED
    assert stored.scope_hash == "c" * 64


@pytest.mark.asyncio
async def test_promote_pending_refuses_a_different_fabric_account() -> None:
    repository = InMemoryFabricGrantRepository()
    await repository.create_grant(_grant().model_copy(update={"account_hash": "f" * 64}))
    pending = _pending()
    await repository.put_pending(pending)

    with pytest.raises(FabricGrantConflict):
        await repository.promote_pending(
            tenant_id=pending.tenant_id,
            owner_object_id=pending.owner_object_id,
            provider=pending.provider,
            receipt=pending.receipt,
            expected_scope_hash=pending.scope_hash,
            expected_audience_hash=pending.audience_hash,
            promoted_cache=pending.cache,
        )


def test_models_reject_plaintext_oauth_fields() -> None:
    with pytest.raises(ValidationError):
        FabricGrantRecord.model_validate({**_grant().model_dump(mode="python"), "refresh_token": "secret"})
    with pytest.raises(ValidationError):
        FabricAuthorizationFlowRecord.model_validate(
            {**_flow().model_dump(mode="python"), "authorization_code": "secret"}
        )
    with pytest.raises(ValidationError):
        FabricPendingGrantRecord.model_validate({**_pending().model_dump(mode="python"), "access_token": "secret"})
