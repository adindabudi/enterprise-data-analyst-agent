from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from eda_api.auth.models import AuthFlowRecord, AuthSessionRecord, Principal
from eda_api.auth.repository import InMemoryAuthRepository
from pydantic import ValidationError


def expires_in(seconds: int) -> datetime:
    return datetime.now(UTC) + timedelta(seconds=seconds)


def test_flow_document_retains_pkce_transaction_with_positive_ttl() -> None:
    flow = AuthFlowRecord(
        id="flow_unit_test_12345678",
        flow={"code_verifier": "synthetic-verifier"},
        expires_at=expires_in(60),
    )

    document = flow.to_document()

    assert document["id"] == flow.id
    assert document["recordType"] == "authFlow"
    assert document["flow"] == {"code_verifier": "synthetic-verifier"}
    assert document["ttl"] >= 1


@pytest.mark.asyncio
async def test_flow_pop_is_one_time_and_replay_returns_none() -> None:
    repository = InMemoryAuthRepository()
    flow = AuthFlowRecord(
        id="flow_one_time_12345678",
        flow={"code_verifier": "synthetic-verifier"},
        expires_at=expires_in(60),
    )
    await repository.put_flow(flow)

    first_pop = await repository.pop_flow(flow.id)
    replay_pop = await repository.pop_flow(flow.id)

    assert first_pop == flow
    assert replay_pop is None


@pytest.mark.asyncio
async def test_expired_session_is_not_returned() -> None:
    repository = InMemoryAuthRepository()
    session = AuthSessionRecord(
        id="auth_expired_1234567890",
        tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
        owner_object_id=UUID("22222222-2222-2222-2222-222222222222"),
        audience=UUID("33333333-3333-3333-3333-333333333333"),
        csrf_sha256="a" * 64,
        expires_at=expires_in(-1),
    )
    await repository.put_session(session)

    result = await repository.get_session(session.id)

    assert result is None


@pytest.mark.parametrize("token_field", ["accessToken", "refreshToken", "idToken", "authorization"])
def test_session_schema_rejects_token_fields(token_field: str) -> None:
    values: dict[str, object] = {
        "id": "auth_schema",
        "tenant_id": "11111111-1111-1111-1111-111111111111",
        "owner_object_id": "22222222-2222-2222-2222-222222222222",
        "audience": "33333333-3333-3333-3333-333333333333",
        "csrf_sha256": "a" * 64,
        "expires_at": expires_in(60),
        token_field: "forbidden",
    }

    with pytest.raises(ValidationError):
        AuthSessionRecord.model_validate(values)


def test_session_document_omits_tokens_and_rejects_unknown_extras() -> None:
    csrf_token = "-".join(("csrf", "token"))
    principal = Principal(
        tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
        owner_object_id=UUID("22222222-2222-2222-2222-222222222222"),
        audience=UUID("33333333-3333-3333-3333-333333333333"),
    )
    session = AuthSessionRecord.create(
        principal,
        csrf_token=csrf_token,
        ttl_seconds=60,
        id="auth_document_1234567890",
    )

    document = session.to_document()

    assert session.principal().tenant_id == session.tenant_id
    assert document["ownerObjectId"] == str(session.owner_object_id)
    assert document["audience"] == str(session.audience)
    json.dumps(document)
    assert set(document).isdisjoint({"accessToken", "refreshToken", "idToken", "authorization"})
    with pytest.raises(ValidationError):
        AuthSessionRecord.model_validate({**session.model_dump(), "unexpected": "value"})
