from __future__ import annotations

import hashlib
import secrets
from typing import Annotated

from fastapi import Cookie, Depends, Header, HTTPException, Request, status

from eda_api.config import Settings
from eda_api.dependencies import auth_repository, settings

from .models import AuthSessionRecord, Principal
from .repository import AuthRepository


async def current_auth_session(
    repository: Annotated[AuthRepository, Depends(auth_repository)],
    auth_session_id: Annotated[str | None, Cookie(alias="eda_session")] = None,
) -> AuthSessionRecord:
    session = await repository.get_session(auth_session_id) if auth_session_id else None
    if session is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="authentication required")
    return session


async def current_principal(
    session: Annotated[AuthSessionRecord, Depends(current_auth_session)],
) -> Principal:
    return session.principal()


async def require_csrf(
    request: Request,
    session: Annotated[AuthSessionRecord, Depends(current_auth_session)],
    config: Annotated[Settings, Depends(settings)],
    origin: Annotated[str | None, Header()] = None,
    csrf_header: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    csrf_cookie: Annotated[str | None, Cookie(alias="eda_csrf")] = None,
) -> Principal:
    del request
    expected_origin = str(config.public_origin).rstrip("/")
    if origin != expected_origin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="origin validation failed")
    if not csrf_header or not csrf_cookie or not secrets.compare_digest(csrf_header, csrf_cookie):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF validation failed")
    actual_hash = hashlib.sha256(csrf_header.encode()).hexdigest()
    if not secrets.compare_digest(actual_hash, session.csrf_sha256):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF validation failed")
    return session.principal()


CurrentAuthSessionDep = Annotated[AuthSessionRecord, Depends(current_auth_session)]
CurrentPrincipalDep = Annotated[Principal, Depends(current_principal)]
CsrfPrincipalDep = Annotated[Principal, Depends(require_csrf)]
