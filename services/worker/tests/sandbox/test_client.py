from __future__ import annotations

import logging
import re

import httpx
import pytest
from azure.core.credentials import AccessToken
from eda_worker.sandbox.client import (
    DynamicSessionClient,
    DynamicSessionError,
    Runtime,
    ValidationProfile,
    create_session_identifier,
)

SESSION_ID = "ds-1234567890abcdef"
SESSION_ID_PATTERN = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9]){0,127}$")


class FakeCredential:
    def __init__(self) -> None:
        self.calls = 0

    async def get_token(self, *scopes: str) -> AccessToken:
        self.calls += 1
        assert scopes == ("https://dynamicsessions.io/.default",)
        return AccessToken(token="token-value", expires_on=4_102_444_800)  # noqa: S106 - synthetic test token


def _client(transport: httpx.AsyncBaseTransport) -> tuple[DynamicSessionClient, FakeCredential]:
    credential = FakeCredential()
    http_client = httpx.AsyncClient(base_url="https://pool.example", transport=transport)
    return DynamicSessionClient("https://pool.example", credential=credential, http_client=http_client), credential


def test_generated_identifiers_match_live_azure_contract_and_are_unique() -> None:
    identifiers = {create_session_identifier() for _ in range(100)}

    assert len(identifiers) == 100
    assert all(identifier.startswith("ds-") for identifier in identifiers)
    assert all(SESSION_ID_PATTERN.fullmatch(identifier) for identifier in identifiers)
    assert all("_" not in identifier for identifier in identifiers)


def test_custom_path_has_identifier_but_no_builtin_api_version() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, request=request))
    client, _ = _client(transport)

    request = client.build_request("POST", SESSION_ID, "/v1/executions")

    assert str(request.url) == f"https://pool.example/v1/executions?identifier={SESSION_ID}"
    assert "api-version" not in request.url.query.decode()


def test_stop_uses_management_contract() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, request=request))
    client, _ = _client(transport)

    request = client.build_stop_request(SESSION_ID)

    assert request.url.path == "/.management/stopSession"
    assert dict(request.url.params) == {"api-version": "2025-02-02-preview", "identifier": SESSION_ID}


def test_logs_never_render_identifier_or_authorization(caplog) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, request=request))
    client, _ = _client(transport)
    caplog.set_level(logging.DEBUG)

    client.log_response(SESSION_ID, status_code=202, trace_id="trace-7")

    assert SESSION_ID not in caplog.text
    assert "Bearer" not in caplog.text
    assert "trace-7" in caplog.text


def test_only_a_response_worth_chasing_reaches_a_production_log(caplog) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, request=request))
    client, _ = _client(transport)
    caplog.set_level(logging.INFO)

    client.log_response(SESSION_ID, status_code=200, trace_id="trace-ok")
    client.log_response(SESSION_ID, status_code=429, trace_id="trace-throttled")

    assert "trace-ok" not in caplog.text
    assert "trace-throttled" in caplog.text


@pytest.mark.asyncio
async def test_import_exec_validate_list_download_and_stop_use_expected_routes_and_headers() -> None:
    seen: list[tuple[str, str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("authorization", "")
        seen.append((request.method, request.url.path, request.url.query.decode()))
        assert auth.startswith("Bearer ")
        if request.url.path == "/v1/files/import":
            return httpx.Response(
                200,
                request=request,
                json={
                    "fileId": "file_imported_12345678",
                    "category": "input",
                    "displayName": "input.bin",
                    "sizeBytes": 4,
                    "sha256": "a" * 64,
                },
            )
        if request.url.path == "/v1/executions" and request.method == "POST":
            return httpx.Response(
                200,
                request=request,
                json={
                    "executionId": "exec_12345678",
                    "status": "succeeded",
                    "runtime": "python",
                    "sourceFileId": "file_imported_12345678",
                    "outputFileIds": ["file_output_12345678"],
                    "stdoutFileId": "file_stdout_12345678",
                    "stderrFileId": None,
                    "returnCode": 0,
                    "durationMs": 2,
                    "peakRssBytes": 3,
                    "cpuTimeMs": 1,
                },
            )
        if request.url.path == "/v1/files" and request.method == "GET":
            return httpx.Response(
                200,
                request=request,
                json=[
                    {
                        "fileId": "file_output_12345678",
                        "category": "output",
                        "displayName": "result.bin",
                        "sizeBytes": 5,
                        "sha256": "b" * 64,
                    }
                ],
            )
        if request.url.path == "/v1/files/file_output_12345678":
            return httpx.Response(200, request=request, content=b"hello")
        if request.url.path == "/v1/files/file_stdout_12345678":
            return httpx.Response(200, request=request, content=b"stdout")
        if request.url.path == "/v1/validations":
            return httpx.Response(
                200,
                request=request,
                json={
                    "validationId": "validation_1234567890abcdef",
                    "fileId": "file_output_12345678",
                    "profile": "core_xlsx",
                    "status": "passed",
                    "report": {
                        "fileId": "file_report_12345678",
                        "category": "validation",
                        "displayName": "report.json",
                        "sizeBytes": 3,
                        "sha256": "c" * 64,
                    },
                },
            )
        if request.url.path == "/v1/executions/exec_12345678":
            return httpx.Response(
                200,
                request=request,
                json={
                    "executionId": "exec_12345678",
                    "status": "succeeded",
                    "runtime": "python",
                    "sourceFileId": "file_imported_12345678",
                    "outputFileIds": ["file_output_12345678"],
                    "stdoutFileId": "file_stdout_12345678",
                    "stderrFileId": None,
                    "returnCode": 0,
                    "durationMs": 2,
                    "peakRssBytes": 3,
                    "cpuTimeMs": 1,
                },
            )
        if request.url.path == "/.management/stopSession":
            return httpx.Response(200, request=request)
        return httpx.Response(500, request=request)

    client, credential = _client(httpx.MockTransport(handler))

    imported = await client.import_bytes(SESSION_ID, "input", "input.bin", b"data")
    execution = await client.execute(SESSION_ID, Runtime.PYTHON, imported.file_id, 30, parameters={"x": 1})
    files = await client.list_files(SESSION_ID)
    downloaded = await client.download_file(SESSION_ID, "file_output_12345678")
    validation = await client.validate(SESSION_ID, "file_output_12345678", ValidationProfile.CORE_XLSX)
    _ = await client.get_execution(SESSION_ID, execution.execution_id)
    await client.stop(SESSION_ID)

    assert imported.file_id == "file_imported_12345678"
    assert execution.execution_id == "exec_12345678"
    assert len(files) == 1
    assert downloaded == b"hello"
    assert validation.status.value == "passed"
    assert credential.calls >= 7
    assert any(path == "/v1/executions" and "identifier=" in query for _, path, query in seen)
    assert any(
        path == "/.management/stopSession" and "api-version=2025-02-02-preview" in query for _, path, query in seen
    )


@pytest.mark.asyncio
async def test_retries_transport_and_5xx_and_429_until_success() -> None:
    attempts = 0
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ReadError("transient", request=request)
        if attempts == 2:
            return httpx.Response(502, request=request)
        return httpx.Response(
            200,
            request=request,
            json={
                "fileId": "file_imported_12345678",
                "category": "input",
                "displayName": "input.bin",
                "sizeBytes": 4,
                "sha256": "a" * 64,
            },
        )

    async def fake_delay(seconds: float) -> None:
        delays.append(seconds)

    credential = FakeCredential()
    http_client = httpx.AsyncClient(base_url="https://pool.example", transport=httpx.MockTransport(handler))
    test_client = DynamicSessionClient(
        "https://pool.example",
        credential=credential,
        http_client=http_client,
        delay=fake_delay,
    )
    imported = await test_client.import_bytes(SESSION_ID, "input", "input.bin", b"data")

    assert imported.file_id == "file_imported_12345678"
    assert attempts == 3
    assert delays == [0.2, 0.4]


@pytest.mark.asyncio
async def test_retries_429_and_fails_after_three_attempts() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(429, request=request)

    credential = FakeCredential()
    http_client = httpx.AsyncClient(base_url="https://pool.example", transport=httpx.MockTransport(handler))
    test_client = DynamicSessionClient("https://pool.example", credential=credential, http_client=http_client)

    with pytest.raises(DynamicSessionError):
        await test_client.import_bytes(SESSION_ID, "input", "input.bin", b"data")
    assert attempts == 3
