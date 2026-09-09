from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from eda_fabric_auth.crypto import CipherEnvelope
from eda_fabric_auth.models import (
    FabricAuthorizationFlowRecord,
    FabricGrantRecord,
    FabricGrantState,
    FabricPendingGrantRecord,
    FabricProvider,
)
from eda_fabric_auth.repository import FabricGrantConflict, InMemoryFabricGrantRepository
from eda_fabric_auth.scopes import ONTOLOGY_BYO_SCOPES, ONTOLOGY_DIRECT_REFERENCE_SCOPE
from pydantic import ValidationError


def envelope() -> CipherEnvelope:
    return CipherEnvelope(
        key_id="https://vault.example/keys/fabric/v1",
        wrapped_dek="AA",
        nonce="AA",
        ciphertext="AA",
    )


def grant(provider: FabricProvider) -> FabricGrantRecord:
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
        cache=envelope(),
        last_used_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )


def flow(provider: FabricProvider, *, expires_at: datetime | None = None) -> FabricAuthorizationFlowRecord:
    return FabricAuthorizationFlowRecord(
        id=FabricAuthorizationFlowRecord.make_id(provider, "callback_state_123"),
        flow_id="callback_state_123",
        tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
        owner_object_id=UUID("22222222-2222-2222-2222-222222222222"),
        provider=provider,
        scope_hash="a" * 64,
        audience_hash="b" * 64,
        correlation_secret_hash="e" * 64,
        flow=envelope(),
        expires_at=expires_at or datetime.now(UTC) + timedelta(minutes=10),
    )


def pending(provider: FabricProvider, *, expires_at: datetime | None = None) -> FabricPendingGrantRecord:
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
        cache=envelope(),
        expires_at=expires_at or datetime.now(UTC) + timedelta(minutes=10),
    )


def test_provider_bound_grant_uses_a_server_derived_identifier() -> None:
    record = grant(FabricProvider.ONTOLOGY)

    assert record.id == "fabric-grant:ontology"
    assert record.provider is FabricProvider.ONTOLOGY


@pytest.mark.parametrize("provider", list(FabricProvider))
def test_provider_grant_rejects_an_identifier_for_another_provider(provider: FabricProvider) -> None:
    with pytest.raises(ValidationError, match="grant ID"):
        FabricGrantRecord.model_validate(
            {
                **grant(provider).model_dump(mode="python"),
                "id": "fabric-grant:semantic_model" if provider is FabricProvider.ONTOLOGY else "fabric-grant:ontology",
            }
        )


@pytest.mark.asyncio
async def test_grants_are_isolated_by_selected_provider() -> None:
    repository = InMemoryFabricGrantRepository()
    ontology_grant = grant(FabricProvider.ONTOLOGY)
    created = await repository.create_grant(ontology_grant)

    assert (
        await repository.get_grant(
            ontology_grant.tenant_id,
            ontology_grant.owner_object_id,
            FabricProvider.ONTOLOGY,
        )
        == created
    )
    assert (
        await repository.get_grant(
            ontology_grant.tenant_id,
            ontology_grant.owner_object_id,
            FabricProvider.SEMANTIC_MODEL,
        )
        is None
    )


@pytest.mark.asyncio
async def test_unlink_removes_only_the_active_provider_grant() -> None:
    repository = InMemoryFabricGrantRepository()
    ontology_grant = grant(FabricProvider.ONTOLOGY)
    semantic_grant = grant(FabricProvider.SEMANTIC_MODEL)
    await repository.create_grant(ontology_grant)
    semantic_created = await repository.create_grant(semantic_grant)

    await repository.delete_grant(
        ontology_grant.tenant_id,
        ontology_grant.owner_object_id,
        FabricProvider.ONTOLOGY,
    )

    assert (
        await repository.get_grant(
            ontology_grant.tenant_id,
            ontology_grant.owner_object_id,
            FabricProvider.ONTOLOGY,
        )
        is None
    )
    assert (
        await repository.get_grant(
            semantic_grant.tenant_id,
            semantic_grant.owner_object_id,
            FabricProvider.SEMANTIC_MODEL,
        )
        == semantic_created
    )


def test_flow_and_pending_records_reject_provider_crossover_identifiers() -> None:
    with pytest.raises(ValidationError, match="flow ID"):
        FabricAuthorizationFlowRecord.model_validate(
            {
                **flow(FabricProvider.ONTOLOGY).model_dump(mode="python"),
                "id": "fabric-flow:semantic_model:callback_state_123",
            }
        )

    with pytest.raises(ValidationError, match="pending grant ID"):
        FabricPendingGrantRecord.model_validate(
            {
                **pending(FabricProvider.ONTOLOGY).model_dump(mode="python"),
                "id": "fabric-pending:semantic_model",
            }
        )


@pytest.mark.asyncio
async def test_flow_is_consumed_once_by_its_server_issued_identifier() -> None:
    repository = InMemoryFabricGrantRepository()
    authorization_flow = flow(FabricProvider.ONTOLOGY)
    await repository.put_flow(authorization_flow)

    consumed = await repository.pop_flow(FabricProvider.ONTOLOGY, authorization_flow.flow_id)
    assert consumed is not None
    assert consumed.id == authorization_flow.id
    assert await repository.pop_flow(FabricProvider.ONTOLOGY, authorization_flow.flow_id) is None


@pytest.mark.asyncio
async def test_flow_identifier_cannot_overwrite_another_provider_binding() -> None:
    repository = InMemoryFabricGrantRepository()
    ontology_flow = flow(FabricProvider.ONTOLOGY)
    semantic_flow = flow(FabricProvider.SEMANTIC_MODEL).model_copy(
        update={
            "flow_id": "callback_state_456",
            "id": FabricAuthorizationFlowRecord.make_id(FabricProvider.SEMANTIC_MODEL, "callback_state_456"),
        }
    )

    await repository.put_flow(ontology_flow)
    await repository.put_flow(semantic_flow)

    assert await repository.pop_flow(FabricProvider.SEMANTIC_MODEL, ontology_flow.flow_id) is None
    assert await repository.pop_flow(FabricProvider.ONTOLOGY, ontology_flow.flow_id) is not None
    assert await repository.pop_flow(FabricProvider.SEMANTIC_MODEL, semantic_flow.flow_id) is not None


@pytest.mark.asyncio
async def test_pending_promotion_requires_the_original_provider_and_bindings() -> None:
    repository = InMemoryFabricGrantRepository()
    pending_grant = pending(FabricProvider.ONTOLOGY)
    await repository.put_pending(pending_grant)

    assert (
        await repository.promote_pending(
            pending_grant.tenant_id,
            pending_grant.owner_object_id,
            FabricProvider.ONTOLOGY,
            pending_grant.receipt,
            pending_grant.scope_hash,
            pending_grant.audience_hash,
            pending_grant.cache,
        )
        is not None
    )
    assert (
        await repository.promote_pending(
            pending_grant.tenant_id,
            pending_grant.owner_object_id,
            FabricProvider.SEMANTIC_MODEL,
            pending_grant.receipt,
            pending_grant.scope_hash,
            pending_grant.audience_hash,
            pending_grant.cache,
        )
        is None
    )


@pytest.mark.asyncio
async def test_pending_scope_audience_mismatch_rejects_promotion() -> None:
    repository = InMemoryFabricGrantRepository()
    pending_grant = pending(FabricProvider.ONTOLOGY)
    await repository.put_pending(pending_grant)

    assert (
        await repository.promote_pending(
            pending_grant.tenant_id,
            pending_grant.owner_object_id,
            FabricProvider.ONTOLOGY,
            pending_grant.receipt,
            "c" * 64,
            pending_grant.audience_hash,
            pending_grant.cache,
        )
        is None
    )


@pytest.mark.asyncio
async def test_expired_flow_and_pending_records_are_unavailable() -> None:
    repository = InMemoryFabricGrantRepository()
    expired_at = datetime.now(UTC) - timedelta(seconds=1)
    expired_flow = flow(FabricProvider.ONTOLOGY, expires_at=expired_at)
    expired_pending = pending(FabricProvider.ONTOLOGY, expires_at=expired_at)
    await repository.put_flow(expired_flow)
    await repository.put_pending(expired_pending)

    assert await repository.pop_flow(expired_flow.provider, expired_flow.flow_id) is None
    assert (
        await repository.promote_pending(
            expired_pending.tenant_id,
            expired_pending.owner_object_id,
            expired_pending.provider,
            expired_pending.receipt,
            expired_pending.scope_hash,
            expired_pending.audience_hash,
            expired_pending.cache,
        )
        is None
    )


@pytest.mark.asyncio
async def test_replace_requires_current_etag_and_rejects_last_write_wins() -> None:
    repository = InMemoryFabricGrantRepository()
    created = await repository.create_grant(grant(FabricProvider.ONTOLOGY))
    assert created.etag is not None

    replacement = created.model_copy(update={"last_used_at": created.last_used_at + timedelta(minutes=1)})
    replaced = await repository.replace_grant(replacement, etag=created.etag)
    assert replaced.etag is not None
    assert replaced.etag != created.etag

    with pytest.raises(FabricGrantConflict, match="ETag mismatch"):
        await repository.replace_grant(replacement, etag=created.etag)


def test_production_ontology_scopes_match_documented_delegated_permissions() -> None:
    assert ONTOLOGY_BYO_SCOPES == (
        "https://analysis.windows.net/powerbi/api/Item.Read.All",
        "https://analysis.windows.net/powerbi/api/Item.Execute.All",
    )
    assert ONTOLOGY_DIRECT_REFERENCE_SCOPE == "https://api.fabric.microsoft.com/.default"
    assert ONTOLOGY_DIRECT_REFERENCE_SCOPE not in ONTOLOGY_BYO_SCOPES
