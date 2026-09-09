from __future__ import annotations

import json

import httpx
import pytest
from azure.core.credentials import AccessToken
from eda_api.hosted_responses import HostedResponsesClient, HostedResponseStatus


class FakeCredential:
    async def get_token(self, *scopes: str, **kwargs: object) -> AccessToken:
        del kwargs
        assert scopes == ("https://ai.azure.com/.default",)
        return AccessToken("entra-token", 4_102_444_800)


@pytest.mark.asyncio
async def test_start_creates_a_stored_background_response_for_the_task() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": "resp_12345678", "status": "queued"})

    transport = httpx.MockTransport(handler)
    client = HostedResponsesClient(
        "https://example.services.ai.azure.com/api/projects/eda/agents/analyst/endpoint/protocols/openai/responses",
        FakeCredential(),
        transport=transport,
    )

    attempt = await client.start(
        "task_12345678",
        user_identity="user_abcdef123456",
        previous_response_id="resp_previous1",
    )

    assert attempt.id == "resp_12345678"
    assert attempt.status is HostedResponseStatus.QUEUED
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert request.url.path.endswith("/responses")
    assert request.url.params["api-version"] == "v1"
    assert request.headers["authorization"] == "Bearer entra-token"
    assert request.headers["x-ms-user-identity"] == "user_abcdef123456"
    assert request.extensions["timeout"]["read"] == 180.0
    body = json.loads(request.content)
    assert body == {
        "input": '{"taskId":"task_12345678"}',
        "background": True,
        "store": True,
        "previous_response_id": "resp_previous1",
        "metadata": {"task_id": "task_12345678"},
    }
    await client.close()


@pytest.mark.asyncio
async def test_get_and_cancel_use_the_same_scoped_response() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        status = "cancelled" if request.url.path.endswith("/cancel") else "in_progress"
        return httpx.Response(200, json={"id": "resp_12345678", "status": status})

    client = HostedResponsesClient(
        "https://example.services.ai.azure.com/api/projects/eda/agents/analyst/endpoint/protocols/openai/responses",
        FakeCredential(),
        transport=httpx.MockTransport(handler),
    )

    observed = await client.get("resp_12345678", user_identity="user_abcdef123456")
    cancelled = await client.cancel("resp_12345678", user_identity="user_abcdef123456")

    assert observed.status is HostedResponseStatus.IN_PROGRESS
    assert cancelled.status is HostedResponseStatus.CANCELLED
    assert [request.method for request in requests] == ["GET", "POST"]
    assert requests[0].url.path.endswith("/responses/resp_12345678")
    assert requests[1].url.path.endswith("/responses/resp_12345678/cancel")
    assert all(request.extensions["timeout"]["read"] == 30.0 for request in requests)
    assert all(request.headers["x-ms-user-identity"] == "user_abcdef123456" for request in requests)
    await client.close()


@pytest.mark.asyncio
async def test_start_accepts_a_container_agent_response_id() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "caresp_12345678", "status": "queued"})

    client = HostedResponsesClient(
        "https://example.services.ai.azure.com/api/projects/eda/agents/analyst/endpoint/protocols/openai/responses",
        FakeCredential(),
        transport=httpx.MockTransport(handler),
    )

    attempt = await client.start("task_12345678", user_identity="user_abcdef123456")

    assert attempt.id == "caresp_12345678"
    await client.close()


@pytest.mark.asyncio
async def test_start_retries_explicit_session_warmup_once_in_the_same_session() -> None:
    requests: list[httpx.Request] = []
    delays: list[float] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(
                424,
                headers={"x-agent-session-id": "session_12345678"},
                json={"error": {"code": "session_not_ready"}},
            )
        return httpx.Response(200, json={"id": "caresp_12345678", "status": "queued"})

    async def sleep(delay: float) -> None:
        delays.append(delay)

    client = HostedResponsesClient(
        "https://example.services.ai.azure.com/api/projects/eda/agents/analyst/endpoint/protocols/openai/responses",
        FakeCredential(),
        transport=httpx.MockTransport(handler),
        sleep=sleep,
    )

    attempt = await client.start("task_12345678", user_identity="user_abcdef123456")

    assert attempt.id == "caresp_12345678"
    assert len(requests) == 2
    assert "x-agent-session-id" not in requests[0].headers
    assert requests[1].headers["x-agent-session-id"] == "session_12345678"
    assert requests[0].content == requests[1].content
    assert delays == [15.0]
    await client.close()


@pytest.mark.parametrize(
    ("headers", "body"),
    [
        ({}, {"error": {"code": "session_not_ready"}}),
        ({"x-agent-session-id": "session_12345678"}, {"error": {"code": "dependency_failed"}}),
    ],
)
@pytest.mark.asyncio
async def test_start_does_not_retry_an_ambiguous_424(
    headers: dict[str, str],
    body: dict[str, object],
) -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(424, headers=headers, json=body)

    client = HostedResponsesClient(
        "https://example.services.ai.azure.com/api/projects/eda/agents/analyst/endpoint/protocols/openai/responses",
        FakeCredential(),
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(httpx.HTTPStatusError):
        await client.start("task_12345678", user_identity="user_abcdef123456")

    assert len(requests) == 1
    await client.close()


@pytest.mark.asyncio
async def test_start_does_not_retry_a_transport_timeout() -> None:
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ReadTimeout("ambiguous POST timeout", request=request)

    client = HostedResponsesClient(
        "https://example.services.ai.azure.com/api/projects/eda/agents/analyst/endpoint/protocols/openai/responses",
        FakeCredential(),
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(httpx.ReadTimeout):
        await client.start("task_12345678", user_identity="user_abcdef123456")

    assert attempts == 1
    await client.close()


@pytest.mark.parametrize("response_id", ["", "../escape", "response with spaces"])
@pytest.mark.asyncio
async def test_response_ids_are_rejected_before_building_a_request(response_id: str) -> None:
    client = HostedResponsesClient(
        "https://example.services.ai.azure.com/api/projects/eda/agents/analyst/endpoint/protocols/openai/responses",
        FakeCredential(),
        transport=httpx.MockTransport(lambda request: httpx.Response(500)),
    )

    with pytest.raises(ValueError, match="response ID"):
        await client.get(response_id, user_identity="user_abcdef123456")
    await client.close()
