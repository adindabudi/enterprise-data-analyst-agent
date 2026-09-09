from __future__ import annotations

import json
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Protocol, cast

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from eda_worker.fabric.ontology.config import OntologyTarget

type JsonValue = dict[str, JsonValue] | list[JsonValue] | str | int | float | bool | None
MAX_MCP_RESULT_BYTES = 2 * 1024 * 1024


class OntologyMcpSession(Protocol):
    async def initialize(self) -> object: ...

    async def list_tools(self) -> object: ...

    async def call_tool(self, name: str, arguments: dict[str, object]) -> object: ...


type SessionOpener = Callable[[str, str], AbstractAsyncContextManager[OntologyMcpSession]]
type TokenProvider = Callable[[], Awaitable[str]]

EXPECTED_ONTOLOGY_TOOLS: dict[str, dict[str, object]] = {
    "list_ontology_entity_types": {
        "type": "object",
        "properties": {
            "entityName": {
                "type": "string",
                "minLength": 1,
                "maxLength": 128,
                "pattern": r"^[a-zA-Z][a-zA-Z0-9_-]{0,127}$",
            },
            "includeProperties": {"type": "boolean"},
        },
        "required": [],
    },
    "search_ontology": {
        "type": "object",
        "properties": {
            "naturalLanguageQuery": {"type": "string", "minLength": 3, "maxLength": 10_000},
            "naturalLanguageResponse": {"type": "boolean"},
        },
        "required": ["naturalLanguageQuery"],
    },
}

EXPECTED_TOOL_DESCRIPTIONS = {
    "list_ontology_entity_types": (
        "Retrieves the specified or all entity types in the ontology and their details—such as property names, "
        "property types, and available telemetry. Helps users explore the schema of the ontology"
    ),
    "search_ontology": (
        "Retrieves the answer to a natural language question from the ontology data estate, returns raw results in "
        "JSON format and optionally a derived natural language answer"
    ),
}


def build_ontology_mcp_url(target: OntologyTarget) -> str:
    return target.endpoint


def validate_ontology_tools(tools: Sequence[Mapping[str, object]]) -> dict[str, dict[str, object]]:
    validated: dict[str, dict[str, object]] = {}
    for tool in tools:
        name = tool.get("name")
        description = tool.get("description")
        schema = tool.get("inputSchema")
        if not isinstance(name, str) or not isinstance(description, str):
            raise ValueError("ontology tool contract is malformed")
        if name in validated:
            raise ValueError("ontology tool contract is malformed")
        if description != EXPECTED_TOOL_DESCRIPTIONS.get(name):
            raise ValueError("ontology tool contract does not match the accepted two-tool contract")
        validated[name] = normalize_tool_schema(json_object(schema))
    if validated != EXPECTED_ONTOLOGY_TOOLS:
        raise ValueError("ontology tool contract does not match the accepted two-tool contract")
    return validated


def normalize_tool_schema(schema: Mapping[str, object]) -> dict[str, object]:
    properties = json_object(schema.get("properties"))
    if schema.get("type") != "object":
        raise ValueError("ontology tool contract is malformed")
    required_names = object_list(schema.get("required", []))
    normalized_properties: dict[str, dict[str, object]] = {}
    for name, property_schema in properties.items():
        property_definition = json_object(property_schema)
        normalized_properties[name] = {
            key: value
            for key, value in property_definition.items()
            if key in {"type", "minLength", "maxLength", "pattern"}
        }
    if not all(isinstance(name, str) for name in required_names):
        raise ValueError("ontology tool contract is malformed")
    return {"type": "object", "properties": normalized_properties, "required": required_names}


class OntologyMcpClient:
    def __init__(
        self,
        target: OntologyTarget,
        *,
        token_provider: TokenProvider,
        session_opener: SessionOpener | None = None,
    ) -> None:
        self._endpoint = build_ontology_mcp_url(target)
        self._token_provider = token_provider
        self._session_opener = session_opener or open_streamable_http_session

    async def inspect(self) -> JsonValue:
        async with self._verified_session() as session:
            return await self._call(session, "list_ontology_entity_types", {"includeProperties": True})

    async def search(self, *, question: str) -> JsonValue:
        async with self._verified_session() as session:
            return await self._search(session, question)

    async def inspect_and_search(self, *, question: str) -> tuple[JsonValue, JsonValue]:
        async with self._verified_session() as session:
            schema = await self._call(session, "list_ontology_entity_types", {"includeProperties": True})
            result = await self._search(session, question)
            return schema, result

    @asynccontextmanager
    async def _verified_session(self) -> AsyncGenerator[OntologyMcpSession]:
        bearer_token = await self._token_provider()
        if not bearer_token:
            raise ValueError("Fabric owner token is required")
        async with self._session_opener(self._endpoint, bearer_token) as session:
            await session.initialize()
            validate_ontology_tools(extract_tools(await session.list_tools()))
            yield session

    async def _search(self, session: OntologyMcpSession, question: str) -> JsonValue:
        if not 3 <= len(question) <= 10_000:
            raise ValueError("ontology question must contain 3-10000 characters")
        return await self._call(
            session,
            "search_ontology",
            {"naturalLanguageQuery": question, "naturalLanguageResponse": False},
        )

    async def _call(self, session: OntologyMcpSession, name: str, arguments: dict[str, object]) -> JsonValue:
        return to_json_value(await session.call_tool(name, arguments))


@asynccontextmanager
async def open_streamable_http_session(url: str, bearer_token: str) -> AsyncGenerator[OntologyMcpSession]:
    async with httpx.AsyncClient(
        headers={"Authorization": f"Bearer {bearer_token}"},
        follow_redirects=False,
        timeout=30,
    ) as http_client:
        async with streamable_http_client(url, http_client=http_client) as (read_stream, write_stream, _):
            async with ClientSession(read_stream, write_stream) as session:
                yield session


def extract_tools(result: object) -> list[Mapping[str, object]]:
    if isinstance(result, (list, tuple)):
        raw_tools = object_list(cast(object, result))
    else:
        raw_tools = object_list(cast(object, getattr(result, "tools", None)))
    if not raw_tools:
        raise ValueError("ontology tool contract is malformed")
    extracted: list[Mapping[str, object]] = []
    for tool in raw_tools:
        value = model_to_json_value(tool)
        extracted.append(json_object(value))
    return extracted


def to_json_value(value: object) -> JsonValue:
    normalized = model_to_json_value(value)
    if isinstance(normalized, Mapping):
        normalized_object = json_object(cast(object, normalized))
        if "structuredContent" in normalized_object:
            normalized = normalized_object["structuredContent"]
    encoded = json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    if len(encoded.encode()) > MAX_MCP_RESULT_BYTES:
        raise ValueError("ontology MCP result exceeds 2 MiB")
    return json_value(cast(object, json.loads(encoded)))


def model_to_json_value(value: object) -> object:
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(by_alias=True, exclude_none=True)
    return value


def json_object(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("ontology MCP payload must be a JSON object")
    raw_mapping = cast(Mapping[object, object], value)
    normalized: dict[str, object] = {}
    for key, item in raw_mapping.items():
        if not isinstance(key, str):
            raise ValueError("ontology MCP payload has a non-string key")
        normalized[key] = item
    return normalized


def object_list(value: object) -> list[object]:
    if isinstance(value, list):
        return [*cast(list[object], value)]
    if isinstance(value, tuple):
        return [*cast(tuple[object, ...], value)]
    raise ValueError("ontology tool contract is malformed")


def json_value(value: object) -> JsonValue:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [json_value(item) for item in object_list(cast(object, value))]
    if isinstance(value, Mapping):
        return {key: json_value(item) for key, item in json_object(cast(object, value)).items()}
    raise ValueError("ontology MCP result is not JSON")
