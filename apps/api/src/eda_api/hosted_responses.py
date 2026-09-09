from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, cast

import httpx
from azure.core.credentials_async import AsyncTokenCredential

_TOKEN_SCOPE = "https://ai.azure.com/.default"  # noqa: S105 - OAuth resource scope, not a secret.
_RESPONSE_ID = re.compile(r"^(?:resp|caresp)_[A-Za-z0-9_-]{8,}$")
_SESSION_ID = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
_TASK_ID = re.compile(r"^task_[A-Za-z0-9_-]{8,}$")


class HostedResponseStatus(StrEnum):
    QUEUED = "queued"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INCOMPLETE = "incomplete"


@dataclass(frozen=True)
class HostedResponseAttempt:
    id: str
    status: HostedResponseStatus


class HostedResponsesClient:
    def __init__(
        self,
        endpoint: str,
        credential: AsyncTokenCredential,
        *,
        api_version: str = "v1",
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = 30.0,
        start_timeout_seconds: float = 180.0,
        warmup_retry_delay_seconds: float = 15.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        normalized = endpoint.rstrip("/")
        if not normalized.startswith("https://") or not normalized.endswith("/responses"):
            raise ValueError("hosted Responses endpoint must be an HTTPS /responses endpoint")
        self._endpoint = normalized
        self._credential = credential
        self._api_version = api_version
        self._start_timeout_seconds = start_timeout_seconds
        self._warmup_retry_delay_seconds = warmup_retry_delay_seconds
        self._sleep = sleep
        self._client = httpx.AsyncClient(transport=transport, timeout=timeout_seconds)

    async def start(
        self,
        task_id: str,
        *,
        user_identity: str,
        previous_response_id: str | None = None,
    ) -> HostedResponseAttempt:
        if not _TASK_ID.fullmatch(task_id):
            raise ValueError("task ID is invalid")
        if previous_response_id is not None:
            self._validate_response_id(previous_response_id)
        body: dict[str, Any] = {
            "input": f'{{"taskId":"{task_id}"}}',
            "background": True,
            "store": True,
            "metadata": {"task_id": task_id},
        }
        if previous_response_id is not None:
            body["previous_response_id"] = previous_response_id
        return await self._request(
            "POST",
            self._endpoint,
            user_identity=user_identity,
            json=body,
            timeout_seconds=self._start_timeout_seconds,
            retry_session_warmup=True,
        )

    async def get(self, response_id: str, *, user_identity: str) -> HostedResponseAttempt:
        self._validate_response_id(response_id)
        return await self._request("GET", f"{self._endpoint}/{response_id}", user_identity=user_identity)

    async def cancel(self, response_id: str, *, user_identity: str) -> HostedResponseAttempt:
        self._validate_response_id(response_id)
        return await self._request(
            "POST",
            f"{self._endpoint}/{response_id}/cancel",
            user_identity=user_identity,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def _request(
        self,
        method: str,
        url: str,
        *,
        user_identity: str,
        json: dict[str, Any] | None = None,
        timeout_seconds: float | None = None,
        retry_session_warmup: bool = False,
    ) -> HostedResponseAttempt:
        if not user_identity or len(user_identity) > 64:
            raise ValueError("hosted user identity is invalid")
        token = await self._credential.get_token(_TOKEN_SCOPE)
        headers = {
            "Authorization": f"Bearer {token.token}",
            "x-ms-user-identity": user_identity,
        }
        request_timeout = timeout_seconds if timeout_seconds is not None else httpx.USE_CLIENT_DEFAULT
        response = await self._client.request(
            method,
            url,
            params={"api-version": self._api_version},
            headers=headers,
            json=json,
            timeout=request_timeout,
        )
        warmup_session_id = self._warmup_session_id(response) if retry_session_warmup else None
        if warmup_session_id is not None:
            await self._sleep(self._warmup_retry_delay_seconds)
            headers["x-agent-session-id"] = warmup_session_id
            response = await self._client.request(
                method,
                url,
                params={"api-version": self._api_version},
                headers=headers,
                json=json,
                timeout=request_timeout,
            )
        response.raise_for_status()
        payload = response.json()
        response_id = payload.get("id")
        raw_status = payload.get("status")
        if not isinstance(response_id, str) or not _RESPONSE_ID.fullmatch(response_id):
            raise ValueError("hosted Responses result has an invalid response ID")
        if not isinstance(raw_status, str):
            raise ValueError("hosted Responses result has no status")
        try:
            status = HostedResponseStatus(raw_status)
        except ValueError as error:
            raise ValueError("hosted Responses result has an unknown status") from error
        return HostedResponseAttempt(id=response_id, status=status)

    @staticmethod
    def _validate_response_id(response_id: str) -> None:
        if not _RESPONSE_ID.fullmatch(response_id):
            raise ValueError("response ID is invalid")

    @staticmethod
    def _warmup_session_id(response: httpx.Response) -> str | None:
        if response.status_code != 424:
            return None
        try:
            payload = cast(object, response.json())
        except ValueError:
            return None
        if not isinstance(payload, dict):
            return None
        error = cast(dict[str, object], payload).get("error")
        if not isinstance(error, dict) or cast(dict[str, object], error).get("code") != "session_not_ready":
            return None
        session_id = response.headers.get("x-agent-session-id")
        if session_id is None or not _SESSION_ID.fullmatch(session_id):
            return None
        return session_id
