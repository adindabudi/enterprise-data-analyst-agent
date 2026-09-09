from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Any, Protocol, cast

import httpx
from azure.core.credentials import AccessToken
from azure.identity.aio import ManagedIdentityCredential
from pydantic import BaseModel, ConfigDict, Field

LOGGER = logging.getLogger(__name__)
MANAGEMENT_API_VERSION = "2025-02-02-preview"
TOKEN_SCOPE = "https://dynamicsessions.io/.default"  # noqa: S105 - OAuth scope URL, not a secret
MAX_REQUEST_BYTES = 10 * 1024 * 1024
MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024
MAX_ATTEMPTS = 3


def create_session_identifier() -> str:
    return f"ds-{secrets.token_hex(32)}"


class SandboxClientModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=lambda value: value.split("_")[0] + "".join(part.title() for part in value.split("_")[1:]),
        extra="forbid",
        frozen=True,
        populate_by_name=True,
    )


class DynamicSessionError(ValueError):
    def __init__(
        self,
        *,
        code: str,
        message: str,
        retryable: bool,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.status_code = status_code


class TokenProvider(Protocol):
    async def get_token(self, *scopes: str) -> AccessToken: ...


class Closable(Protocol):
    async def close(self) -> None: ...


DelayHook = Callable[[float], Awaitable[None]]


class Runtime(StrEnum):
    PYTHON = "python"
    JAVASCRIPT = "javascript"


class ValidationProfile(StrEnum):
    CORE_XLSX = "core_xlsx"
    CORE_HTML = "core_html"
    WEB_ARTIFACT_HTML = "web_artifact_html"
    CORE_CHART = "core_chart"
    CORE_MERMAID = "core_mermaid"
    PROVENANCE = "provenance"
    DOCUMENT_PPTX = "document_pptx"
    DOCUMENT_DOCX = "document_docx"
    DOCUMENT_XLSX = "document_xlsx"
    DOCUMENT_PDF = "document_pdf"


class ValidationStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"


class FileRecord(SandboxClientModel):
    file_id: str = Field(pattern=r"^file_[A-Za-z0-9_-]{8,}$")
    category: str
    display_name: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class ExecutionRecord(SandboxClientModel):
    execution_id: str
    status: str
    runtime: Runtime
    source_file_id: str
    stdout_file_id: str | None = None
    stderr_file_id: str | None = None
    output_file_ids: tuple[str, ...] = ()
    return_code: int | None = None
    duration_ms: int = Field(ge=0)
    peak_rss_bytes: int = Field(ge=0)
    cpu_time_ms: int = Field(ge=0)


class ValidationResult(SandboxClientModel):
    validation_id: str
    file_id: str
    profile: ValidationProfile
    status: ValidationStatus
    report: FileRecord


class DynamicSessionClient:
    def __init__(
        self,
        pool_management_endpoint: str,
        *,
        credential: TokenProvider | None = None,
        http_client: httpx.AsyncClient | None = None,
        delay: DelayHook | None = None,
        control_timeout_seconds: float = 30,
        operation_timeout_seconds: float = 30,
        max_request_bytes: int = MAX_REQUEST_BYTES,
        max_download_bytes: int = MAX_DOWNLOAD_BYTES,
    ) -> None:
        self._credential = credential or ManagedIdentityCredential()
        self._owns_credential = credential is None
        self._client = http_client or httpx.AsyncClient(base_url=pool_management_endpoint.rstrip("/"))
        self._owns_client = http_client is None
        self._delay = delay or asyncio.sleep
        self._control_timeout = control_timeout_seconds
        self._operation_timeout = operation_timeout_seconds
        self._max_request_bytes = max_request_bytes
        self._max_download_bytes = max_download_bytes

    @staticmethod
    def create_identifier() -> str:
        return create_session_identifier()

    async def allocate(self, task_id: str, proposed_identifier: str) -> str:
        del task_id
        return proposed_identifier

    def build_request(self, method: str, identifier: str, path: str) -> httpx.Request:
        if not path.startswith("/v1/"):
            raise ValueError("custom session path must start with /v1/")
        return self._client.build_request(method, path, params={"identifier": identifier})

    def build_stop_request(self, identifier: str) -> httpx.Request:
        return self._client.build_request(
            "POST",
            "/.management/stopSession",
            params={"api-version": MANAGEMENT_API_VERSION, "identifier": identifier},
        )

    def log_response(self, identifier: str, *, status_code: int, trace_id: str | None) -> None:
        del identifier
        # Every file import, execution, validation and download passes through here, so a healthy
        # response is a debug detail; only the ones worth chasing are worth a production line.
        level = logging.WARNING if status_code >= 400 else logging.DEBUG
        LOGGER.log(level, "dynamic session response status=%s trace_id=%s", status_code, trace_id)

    async def import_bytes(self, identifier: str, category: str, display_name: str, body: bytes) -> FileRecord:
        self._bound_payload_size(body)
        files = {
            "file": (
                display_name,
                body,
                "application/octet-stream",
            )
        }
        response = await self._request(
            "POST",
            "/v1/files/import",
            identifier=identifier,
            params={"category": category},
            files=files,
            timeout=self._operation_timeout,
        )
        return FileRecord.model_validate(response.json())

    async def execute(
        self,
        identifier: str,
        runtime: Runtime,
        source_file_id: str,
        timeout_seconds: int,
        parameters: dict[str, str | int | float | bool | None] | None = None,
    ) -> ExecutionRecord:
        payload: dict[str, Any] = {
            "runtime": runtime.value,
            "sourceFileId": source_file_id,
            "timeoutSeconds": timeout_seconds,
        }
        if parameters:
            encoded = json.dumps(parameters, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
            self._bound_payload_size(encoded)
            parameter_file = await self.import_bytes(identifier, "input", "parameters.json", encoded)
            payload["parametersFileId"] = parameter_file.file_id
        response = await self._request(
            "POST",
            "/v1/executions",
            identifier=identifier,
            json_payload=payload,
            timeout=self._operation_timeout,
        )
        return ExecutionRecord.model_validate(response.json())

    async def list_files(self, identifier: str) -> tuple[FileRecord, ...]:
        response = await self._request(
            "GET",
            "/v1/files",
            identifier=identifier,
            timeout=self._control_timeout,
        )
        body_obj: object = response.json()
        if not isinstance(body_obj, list):
            raise DynamicSessionError(
                code="invalid_response", message="file listing response is malformed", retryable=False
            )
        records: list[FileRecord] = []
        body = cast(list[object], body_obj)
        for item in body:
            if not isinstance(item, dict):
                raise DynamicSessionError(
                    code="invalid_response", message="file listing response item is malformed", retryable=False
                )
            records.append(FileRecord.model_validate(cast(dict[str, object], item)))
        return tuple(records)

    async def describe_file(self, identifier: str, file_id: str) -> FileRecord:
        for record in await self.list_files(identifier):
            if record.file_id == file_id:
                return record
        raise DynamicSessionError(code="file_not_found", message="file ID is unavailable", retryable=False)

    async def download_file(self, identifier: str, file_id: str) -> bytes:
        response = await self._request(
            "GET",
            f"/v1/files/{file_id}",
            identifier=identifier,
            timeout=self._operation_timeout,
            allow_404=False,
        )
        content = response.content
        if len(content) > self._max_download_bytes:
            raise DynamicSessionError(
                code="download_too_large", message="download exceeded byte limit", retryable=False
            )
        return content

    async def validate(self, identifier: str, file_id: str, profile: ValidationProfile) -> ValidationResult:
        payload = {"fileId": file_id, "profile": profile.value}
        response = await self._request(
            "POST",
            "/v1/validations",
            identifier=identifier,
            json_payload=payload,
            timeout=self._operation_timeout,
        )
        return ValidationResult.model_validate(response.json())

    async def get_execution(self, identifier: str, execution_id: str) -> ExecutionRecord:
        response = await self._request(
            "GET",
            f"/v1/executions/{execution_id}",
            identifier=identifier,
            timeout=self._control_timeout,
            allow_404=False,
        )
        return ExecutionRecord.model_validate(response.json())

    async def stop(self, identifier: str) -> None:
        await self._request(
            "POST",
            "/.management/stopSession",
            identifier=identifier,
            params={"api-version": MANAGEMENT_API_VERSION},
            timeout=self._control_timeout,
            management=True,
            allow_404=True,
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()
        if self._owns_credential:
            closer = cast(Closable | None, self._credential if hasattr(self._credential, "close") else None)
            if closer is not None:
                await closer.close()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        identifier: str,
        params: dict[str, str] | None = None,
        json_payload: dict[str, Any] | None = None,
        files: dict[str, tuple[str, bytes, str]] | None = None,
        timeout: float,  # noqa: ASYNC109 - forwarded to the underlying httpx request timeout
        management: bool = False,
        allow_404: bool = False,
    ) -> httpx.Response:
        request_params = dict(params or {})
        request_params["identifier"] = identifier
        token = await self._credential.get_token(TOKEN_SCOPE)
        headers = {"Authorization": f"Bearer {token.token}"}
        attempts = 0
        last_error: Exception | None = None
        while attempts < MAX_ATTEMPTS:
            attempts += 1
            attempt_start = time.monotonic()
            try:
                response = await self._client.request(
                    method,
                    path,
                    params=request_params,
                    headers=headers,
                    json=json_payload,
                    files=files,
                    timeout=timeout,
                )
                trace_id = response.headers.get("x-ms-request-id") or response.headers.get("x-ms-client-request-id")
                self.log_response(identifier, status_code=response.status_code, trace_id=trace_id)
                if self._is_retryable_status(response.status_code):
                    if attempts < MAX_ATTEMPTS:
                        await self._delay(0.2 * attempts)
                        continue
                    raise DynamicSessionError(
                        code="http_retry_exhausted",
                        message="sandbox request retry budget exhausted",
                        retryable=True,
                        status_code=response.status_code,
                    )
                if response.status_code >= 400:
                    if allow_404 and response.status_code == 404:
                        return response
                    raise DynamicSessionError(
                        code=self._error_code_for_status(response.status_code, management=management),
                        message="sandbox request failed",
                        retryable=False,
                        status_code=response.status_code,
                    )
                return response
            except httpx.TransportError as error:
                last_error = error
                if attempts < MAX_ATTEMPTS:
                    await self._delay(0.2 * attempts)
                    continue
                raise DynamicSessionError(
                    code="transport_retry_exhausted",
                    message="sandbox transport retry budget exhausted",
                    retryable=True,
                ) from error
            finally:
                duration_ms = int((time.monotonic() - attempt_start) * 1000)
                LOGGER.debug("dynamic session request duration_ms=%s attempt=%s", duration_ms, attempts)
        if last_error is not None:
            raise DynamicSessionError(
                code="request_failed", message="sandbox request failed", retryable=True
            ) from last_error
        raise DynamicSessionError(code="request_failed", message="sandbox request failed", retryable=True)

    def _bound_payload_size(self, payload: bytes) -> None:
        if len(payload) > self._max_request_bytes:
            raise DynamicSessionError(code="payload_too_large", message="request exceeded byte limit", retryable=False)

    @staticmethod
    def _is_retryable_status(status_code: int) -> bool:
        return status_code == 429 or status_code >= 500

    @staticmethod
    def _error_code_for_status(status_code: int, *, management: bool) -> str:
        if status_code == 400:
            return "management_bad_request" if management else "sandbox_bad_request"
        if status_code == 401:
            return "unauthorized"
        if status_code == 403:
            return "forbidden"
        if status_code == 404:
            return "not_found"
        return "sandbox_http_error"
