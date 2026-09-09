from __future__ import annotations

import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Cookie, Depends, Form, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

from eda_api.config import Settings
from eda_api.dependencies import auth_repository, msal_client, settings

from .dependencies import CsrfPrincipalDep, CurrentAuthSessionDep
from .models import AuthFlowRecord, AuthSessionRecord
from .msal_client import AuthenticationError, MsalAuthClient
from .repository import AuthRepository

router = APIRouter(prefix="/api/auth", tags=["auth"])


class AuthSessionInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["authenticated"]
    expires_at: str = Field(serialization_alias="expiresAt")


@router.get("/login", status_code=status.HTTP_307_TEMPORARY_REDIRECT, response_class=RedirectResponse)
async def login(
    repository: Annotated[AuthRepository, Depends(auth_repository)],
    client: Annotated[MsalAuthClient, Depends(msal_client)],
    config: Annotated[Settings, Depends(settings)],
) -> RedirectResponse:
    flow = await client.initiate()
    state_value = flow.get("state")
    auth_uri = flow.get("auth_uri")
    if not isinstance(state_value, str) or not isinstance(auth_uri, str):
        raise AuthenticationError("authorization flow initialization failed")
    await repository.put_flow(
        AuthFlowRecord(
            id=f"flow_{state_value}",
            flow=flow,
            expires_at=datetime.now(UTC) + timedelta(seconds=config.auth_flow_ttl_seconds),
        )
    )
    response = RedirectResponse(auth_uri, status_code=status.HTTP_307_TEMPORARY_REDIRECT)
    response.set_cookie(
        "eda_auth_flow",
        state_value,
        max_age=config.auth_flow_ttl_seconds,
        secure=config.cookie_secure,
        httponly=True,
        samesite="none" if config.cookie_secure else "lax",
        path="/api/auth/callback",
    )
    return response


@router.post("/callback", response_class=RedirectResponse, status_code=status.HTTP_303_SEE_OTHER)
async def callback(
    repository: Annotated[AuthRepository, Depends(auth_repository)],
    client: Annotated[MsalAuthClient, Depends(msal_client)],
    config: Annotated[Settings, Depends(settings)],
    state_value: Annotated[str, Form(alias="state")],
    flow_cookie: Annotated[str | None, Cookie(alias="eda_auth_flow")] = None,
    code: Annotated[str | None, Form()] = None,
    error: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    if flow_cookie is None or not hmac.compare_digest(flow_cookie, state_value):
        raise AuthenticationError("authorization flow browser correlation failed")
    flow = await repository.pop_flow(f"flow_{state_value}")
    if flow is None:
        raise AuthenticationError("authorization flow expired or already consumed")
    response_values = {"state": state_value}
    if code:
        response_values["code"] = code
    if error:
        response_values["error"] = error
    principal = await client.complete(flow.flow, response_values)
    csrf_token = secrets.token_urlsafe(32)
    session = AuthSessionRecord.create(principal, csrf_token, config.auth_session_ttl_seconds)
    await repository.put_session(session)
    response = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie("eda_auth_flow", path="/api/auth/callback")
    response.set_cookie(
        "eda_session",
        session.id,
        max_age=config.auth_session_ttl_seconds,
        secure=config.cookie_secure,
        httponly=True,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        "eda_csrf",
        csrf_token,
        max_age=config.auth_session_ttl_seconds,
        secure=config.cookie_secure,
        httponly=False,
        samesite="strict",
        path="/",
    )
    return response


@router.get("/session", response_model=AuthSessionInfo)
async def session_info(session: CurrentAuthSessionDep) -> AuthSessionInfo:
    return AuthSessionInfo(status="authenticated", expires_at=session.expires_at.isoformat())


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    principal: CsrfPrincipalDep,
    session: CurrentAuthSessionDep,
    repository: Annotated[AuthRepository, Depends(auth_repository)],
) -> None:
    del principal
    await repository.delete_session(session.id)
    response.delete_cookie("eda_session", path="/")
    response.delete_cookie("eda_csrf", path="/")
