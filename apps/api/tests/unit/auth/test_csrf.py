from __future__ import annotations

import asyncio
from uuid import UUID

import pytest
from eda_api.auth.models import AuthSessionRecord, Principal
from eda_api.auth.repository import InMemoryAuthRepository
from fastapi.testclient import TestClient


@pytest.fixture
def authenticated_client(client: TestClient, auth_repository: InMemoryAuthRepository) -> TestClient:
    csrf_token = "-".join(("known", "csrf"))
    session = AuthSessionRecord.create(
        Principal(
            tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
            owner_object_id=UUID("22222222-2222-2222-2222-222222222222"),
            audience=UUID("33333333-3333-3333-3333-333333333333"),
        ),
        csrf_token=csrf_token,
        ttl_seconds=300,
        id="auth_csrf_session_1234567890",
    )
    asyncio.run(auth_repository.put_session(session))
    client.cookies.set("eda_session", session.id)
    client.cookies.set("eda_csrf", csrf_token)
    return client


def test_state_change_rejects_missing_origin(authenticated_client: TestClient) -> None:
    response = authenticated_client.post("/api/auth/logout", headers={"X-CSRF-Token": "known-csrf"})

    assert response.status_code == 403


def test_state_change_rejects_cross_origin(authenticated_client: TestClient) -> None:
    response = authenticated_client.post(
        "/api/auth/logout",
        headers={"Origin": "https://attacker.example", "X-CSRF-Token": "known-csrf"},
    )

    assert response.status_code == 403


def test_state_change_accepts_matching_origin_cookie_and_header(authenticated_client: TestClient) -> None:
    response = authenticated_client.post(
        "/api/auth/logout",
        headers={"Origin": "https://analyst.example.test", "X-CSRF-Token": "known-csrf"},
    )

    assert response.status_code == 204
