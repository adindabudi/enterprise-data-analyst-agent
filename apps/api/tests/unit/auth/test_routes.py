from __future__ import annotations

import asyncio
from uuid import UUID

from eda_api.auth.models import AuthSessionRecord, Principal
from eda_api.auth.repository import InMemoryAuthRepository
from fastapi.testclient import TestClient


def test_login_redirects_only_to_msal_auth_uri(client: TestClient) -> None:
    response = client.get("/api/auth/login", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"].startswith("https://login.microsoftonline.com/")


def test_callback_sets_only_opaque_secure_cookies(client: TestClient) -> None:
    client.get("/api/auth/login", follow_redirects=False)

    response = client.post(
        "/api/auth/callback",
        data={"state": "state-12345678", "code": "one-time-code"},
        follow_redirects=False,
    )

    cookies = response.headers.get_list("set-cookie")
    assert response.status_code == 303
    assert any("eda_session=auth_" in value and "HttpOnly" in value and "Secure" in value for value in cookies)
    assert any("eda_csrf=" in value and "HttpOnly" not in value and "Secure" in value for value in cookies)
    assert all("22222222-2222-2222-2222-222222222222" not in value for value in cookies)


def test_callback_replay_is_unauthorized(client: TestClient) -> None:
    client.get("/api/auth/login", follow_redirects=False)
    callback_values = {"state": "state-12345678", "code": "one-time-code"}

    first_response = client.post("/api/auth/callback", data=callback_values, follow_redirects=False)
    replay_response = client.post("/api/auth/callback", data=callback_values, follow_redirects=False)

    assert first_response.status_code == 303
    assert replay_response.status_code == 401


def test_callback_requires_initiating_browser_without_consuming_flow(client: TestClient) -> None:
    client.get("/api/auth/login", follow_redirects=False)
    correlation = client.cookies.get("eda_auth_flow")
    assert correlation is not None
    client.cookies.delete("eda_auth_flow")

    rejected = client.post(
        "/api/auth/callback",
        data={"state": "state-12345678", "code": "one-time-code"},
        follow_redirects=False,
    )
    client.cookies.set("eda_auth_flow", correlation)
    accepted = client.post(
        "/api/auth/callback",
        data={"state": "state-12345678", "code": "one-time-code"},
        follow_redirects=False,
    )

    assert rejected.status_code == 401
    assert accepted.status_code == 303


def test_unknown_session_cookie_is_unauthorized(client: TestClient) -> None:
    response = client.get("/api/auth/session", cookies={"eda_session": "auth_unknown-session-value"})

    assert response.status_code == 401


def test_unhandled_error_is_filtered_through_api_problem(client: TestClient) -> None:
    async def raise_unhandled() -> None:
        raise RuntimeError("sentinel exception text")

    client.app.add_api_route("/test/unhandled", raise_unhandled, methods=["GET"])

    response = client.get("/test/unhandled")

    assert response.status_code == 500
    assert response.json() == {
        "type": "about:blank",
        "title": "Internal server error",
        "status": 500,
        "code": "internal_error",
        "correlationId": response.headers["X-Correlation-ID"],
        "detail": None,
    }
    assert "sentinel exception text" not in response.text


def test_session_returns_only_public_session_information(
    client: TestClient, auth_repository: InMemoryAuthRepository
) -> None:
    csrf_token = "-".join(("known", "csrf"))
    session = AuthSessionRecord.create(
        Principal(
            tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
            owner_object_id=UUID("22222222-2222-2222-2222-222222222222"),
            audience=UUID("33333333-3333-3333-3333-333333333333"),
        ),
        csrf_token=csrf_token,
        ttl_seconds=300,
        id="auth_session_info_1234567890",
    )
    asyncio.run(auth_repository.put_session(session))

    response = client.get("/api/auth/session", cookies={"eda_session": session.id})

    assert response.status_code == 200
    assert response.json() == {"status": "authenticated", "expiresAt": session.expires_at.isoformat()}
