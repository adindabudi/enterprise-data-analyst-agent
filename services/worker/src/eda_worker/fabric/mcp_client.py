from __future__ import annotations

import json
import re
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Protocol, cast
from uuid import UUID

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from eda_worker.fabric.config import FABRIC_IQ_MCP_URL, FABRIC_IQ_VARIANT
from eda_worker.fabric.errors import FabricProviderError

type JsonValue = dict[str, JsonValue] | list[JsonValue] | str | int | float | bool | None
type TokenProvider = Callable[[], Awaitable[str]]
MAX_MCP_RESULT_BYTES = 2 * 1024 * 1024
RUNTIME_TOOL_NAMES = frozenset({"GetSemanticModelSchema", "ValueSearch", "ExecuteQuery"})
EXPECTED_TOOL_NAMES = frozenset(
    {
        "DiscoverArtifacts",
        "GetReportMetadata",
        "GetSemanticModelSchema",
        "ValueSearch",
        "ExecuteQuery",
        "ResolveReportIdFromUrl",
    }
)
_UUID_PATTERN = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")


class FabricMcpSession(Protocol):
    async def initialize(self) -> object: ...

    async def list_tools(self) -> object: ...

    async def call_tool(self, name: str, arguments: dict[str, object]) -> object: ...


type SessionOpener = Callable[[str, dict[str, str]], AbstractAsyncContextManager[FabricMcpSession]]


class FabricMcpToolError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class FabricMcpClient:
    def __init__(
        self,
        *,
        token_provider: TokenProvider,
        expected_tools: Sequence[Mapping[str, object]],
        session_opener: SessionOpener | None = None,
    ) -> None:
        self._token_provider = token_provider
        self._expected_tools = tuple(expected_tools)
        validate_fabric_tools(self._expected_tools, self._expected_tools)
        self._session_opener = session_opener or open_streamable_http_session

    async def discover_tools(self) -> dict[str, dict[str, object]]:
        bearer_token = await self._token_provider()
        if not bearer_token:
            raise ValueError("Fabric owner token is required")
        headers = _request_headers(bearer_token)
        try:
            async with self._session_opener(FABRIC_IQ_MCP_URL, headers) as session:
                await session.initialize()
                return validate_fabric_tools(extract_tools(await session.list_tools()), self._expected_tools)
        except httpx.HTTPStatusError as error:
            raise _provider_http_error(error) from None

    async def call(
        self,
        name: str,
        *,
        semantic_model_id: UUID,
        arguments: Mapping[str, object],
    ) -> JsonValue:
        if name not in RUNTIME_TOOL_NAMES:
            raise ValueError("Fabric tool is outside the runtime allowlist")
        trusted_arguments = _bind_semantic_model(arguments, semantic_model_id)
        bearer_token = await self._token_provider()
        if not bearer_token:
            raise ValueError("Fabric owner token is required")
        headers = _request_headers(bearer_token)
        try:
            async with self._session_opener(FABRIC_IQ_MCP_URL, headers) as session:
                await session.initialize()
                validate_fabric_tools(extract_tools(await session.list_tools()), self._expected_tools)
                return to_json_value(await session.call_tool(name, trusted_arguments))
        except httpx.HTTPStatusError as error:
            raise _provider_http_error(error) from None


@asynccontextmanager
async def open_streamable_http_session(
    url: str,
    headers: dict[str, str],
) -> AsyncGenerator[FabricMcpSession]:
    async with httpx.AsyncClient(
        headers=headers,
        follow_redirects=False,
        timeout=httpx.Timeout(30, read=120),
    ) as http_client:
        async with streamable_http_client(url, http_client=http_client) as transport:
            read_stream, write_stream, _ = transport
            async with ClientSession(read_stream, write_stream) as session:
                yield session


def validate_fabric_tools(
    tools: Sequence[Mapping[str, object]],
    expected_tools: Sequence[Mapping[str, object]],
) -> dict[str, dict[str, object]]:
    expected = _normalize_tools(expected_tools)
    actual = _normalize_tools(tools)
    if frozenset(expected) != EXPECTED_TOOL_NAMES or actual != expected:
        raise ValueError("Fabric provider does not match the pinned six-tool contract")
    return actual


def extract_tools(result: object) -> list[Mapping[str, object]]:
    if isinstance(result, (list, tuple)):
        raw_tools = object_list(cast(object, result))
    else:
        raw_tools = object_list(cast(object, getattr(result, "tools", None)))
    if not raw_tools:
        raise ValueError("Fabric provider six-tool contract is malformed")
    return [json_object(model_to_json_value(tool)) for tool in raw_tools]


def to_json_value(value: object) -> JsonValue:
    normalized = model_to_json_value(value)
    if isinstance(normalized, Mapping):
        result_object = json_object(cast(object, normalized))
        if result_object.get("isError") is True:
            _raise_tool_error(result_object)
        if "structuredContent" in result_object:
            normalized = result_object["structuredContent"]
    encoded = json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    if len(encoded.encode("utf-8")) > MAX_MCP_RESULT_BYTES:
        raise ValueError("Fabric MCP result exceeds 2 MiB")
    return json_value(cast(object, json.loads(encoded)))


def model_to_json_value(value: object) -> object:
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(by_alias=True, exclude_none=True)
    return value


def json_object(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("Fabric MCP payload must be a JSON object")
    normalized: dict[str, object] = {}
    for key, item in cast(Mapping[object, object], value).items():
        if not isinstance(key, str):
            raise ValueError("Fabric MCP payload has a non-string key")
        normalized[key] = item
    return normalized


def object_list(value: object) -> list[object]:
    if isinstance(value, list):
        return [*cast(list[object], value)]
    if isinstance(value, tuple):
        return [*cast(tuple[object, ...], value)]
    raise ValueError("Fabric provider six-tool contract is malformed")


def json_value(value: object) -> JsonValue:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [json_value(item) for item in object_list(cast(object, value))]
    if isinstance(value, Mapping):
        return {key: json_value(item) for key, item in json_object(cast(object, value)).items()}
    raise ValueError("Fabric MCP result is not JSON")


def _normalize_tools(tools: Sequence[Mapping[str, object]]) -> dict[str, dict[str, object]]:
    normalized: dict[str, dict[str, object]] = {}
    for tool in tools:
        name = tool.get("name")
        description = tool.get("description")
        schema = tool.get("inputSchema")
        if not isinstance(name, str) or not isinstance(description, str) or not description.strip():
            raise ValueError("Fabric provider six-tool contract is malformed")
        if name in normalized:
            raise ValueError("Fabric provider six-tool contract is malformed")
        normalized_schema = _canonical_json_object(schema)
        if normalized_schema.get("type") != "object":
            raise ValueError("Fabric provider six-tool contract is malformed")
        normalized[name] = {
            "name": name,
            "description": description,
            "inputSchema": normalized_schema,
        }
    return {name: normalized[name] for name in sorted(normalized)}


def _canonical_json_object(value: object) -> dict[str, object]:
    normalized = json_object(value)
    encoded = json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    decoded = cast(object, json.loads(encoded))
    return json_object(decoded)


def _bind_semantic_model(arguments: Mapping[str, object], semantic_model_id: UUID) -> dict[str, object]:
    trusted_id = str(semantic_model_id)
    provided_id = arguments.get("artifactId")
    if provided_id is not None and provided_id != trusted_id:
        raise ValueError("Fabric call conflicts with the trusted semantic model")
    for value in arguments.values():
        _reject_foreign_identifiers(value, trusted_id)
    return {**arguments, "artifactId": trusted_id}


def _reject_foreign_identifiers(value: object, trusted_id: str) -> None:
    if isinstance(value, str):
        if any(identifier.lower() != trusted_id for identifier in _UUID_PATTERN.findall(value)):
            raise ValueError("Fabric call conflicts with the trusted semantic model")
        return
    if isinstance(value, Mapping):
        for nested in cast(Mapping[object, object], value).values():
            _reject_foreign_identifiers(nested, trusted_id)
        return
    if isinstance(value, (list, tuple)):
        for nested in cast(Sequence[object], value):
            _reject_foreign_identifiers(nested, trusted_id)


def _request_headers(bearer_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {bearer_token}",
        "X-VARIANTS": FABRIC_IQ_VARIANT,
    }


def _raise_tool_error(result: Mapping[str, object]) -> None:
    structured = result.get("structuredContent")
    error = json_object(cast(object, structured)) if isinstance(structured, Mapping) else {}
    code = error.get("code")
    message = error.get("message")
    safe_code = code[:80] if isinstance(code, str) and code else "fabric_mcp_error"
    safe_message = message[:500] if isinstance(message, str) and message else "Fabric MCP operation failed"
    raise FabricMcpToolError(safe_code, safe_message)


def _provider_http_error(error: httpx.HTTPStatusError) -> FabricProviderError:
    status_code = error.response.status_code
    retry_after_value = error.response.headers.get("Retry-After")
    retry_after: float | None = None
    if retry_after_value is not None:
        try:
            parsed = float(retry_after_value)
        except ValueError:
            parsed = -1
        if 0 <= parsed <= 30:
            retry_after = parsed
    return FabricProviderError(
        status_code=status_code,
        code=f"fabric_http_{status_code}",
        retry_after=retry_after,
    )
