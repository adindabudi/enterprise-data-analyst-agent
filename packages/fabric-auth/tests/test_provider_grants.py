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


def test_provider_grant_rejects_an_identifier_for_another_provider() -> None:
    with pytest.raises(ValidationError, match="grant ID"):
        FabricGrantRecord.model_validate(
            {
                **grant(FabricProvider.ONTOLOGY).model_dump(mode="python"),
                "id": "fabric-grant:other",
            }
        )


def test_flow_and_pending_records_reject_provider_crossover_identifiers() -> None:
    with pytest.raises(ValidationError, match="flow ID"):
        FabricAuthorizationFlowRecord.model_validate(
            {
                **flow(FabricProvider.ONTOLOGY).model_dump(mode="python"),
                "id": "fabric-flow:other:callback_state_123",
            }
        )

    with pytest.raises(ValidationError, match="pending grant ID"):
        FabricPendingGrantRecord.model_validate(
            {
                **pending(FabricProvider.ONTOLOGY).model_dump(mode="python"),
                "id": "fabric-pending:other",
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
async def test_pending_promotion_with_matching_bindings_creates_the_grant() -> None:
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
