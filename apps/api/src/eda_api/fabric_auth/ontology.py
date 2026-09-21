from __future__ import annotations

import json
from collections.abc import AsyncGenerator, Callable, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Protocol, cast

import httpx
from eda_runtime_state.models import TaskPartition
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from eda_api.chat.service import FabricQueryUpdate
from eda_api.fabric_ontology import OntologyTarget

# The source picks its own projection, so the same question came back as 4.7 KB or 21.2 KB across runs.
# At 8 KB the wide shape lost half its rows and the answer counted 40 of 63.
MAX_ONTOLOGY_RESULT_CHARS = 24_000
# Multi-entity operational sources need their units and grains, not just a compact list of names.
MAX_ONTOLOGY_SCHEMA_CHARS = 24_000
MAX_ONTOLOGY_QUERY_CHARS = 10_000


EXPECTED_ONTOLOGY_TOOLS: dict[str, dict[str, object]] = {
    "list_ontology_entity_types": {
        "description": (
            "Retrieves the specified or all entity types in the ontology and their details—such as property names, "
            "property types, and available telemetry. Helps users explore the schema of the ontology"
        ),
        "inputSchema": {
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
    },
    "search_ontology": {
        "description": (
            "Retrieves the answer to a natural language question from the ontology data estate, returns raw results in "
            "JSON format and optionally a derived natural language answer"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "naturalLanguageQuery": {"type": "string", "minLength": 3, "maxLength": 10_000},
                "naturalLanguageResponse": {"type": "boolean"},
            },
            "required": ["naturalLanguageQuery"],
        },
    },
}


class OntologyMcpSession(Protocol):
    async def initialize(self) -> object: ...

    async def list_tools(self) -> object: ...

    async def call_tool(self, name: str, arguments: dict[str, object]) -> object: ...


class FabricTokenProvider(Protocol):
    async def acquire(self, partition: TaskPartition) -> str: ...


type SessionOpener = Callable[[str, str], AbstractAsyncContextManager[OntologyMcpSession]]


class FabricOntologyQueryService:
    def __init__(
        self,
        targets: Mapping[str, OntologyTarget],
        *,
        token_provider: FabricTokenProvider,
        session_opener: SessionOpener | None = None,
    ) -> None:
        if len(targets) != 1:
            raise ValueError("interactive ontology smoke requires exactly one target")
        self._alias, self._target = next(iter(targets.items()))
        self._token_provider = token_provider
        self._session_opener = session_opener or open_streamable_http_session
        self._schema: str | None = None
        # The tool contract belongs to the endpoint, not to a call, and listing it costs about
        # three seconds of every query. Confirm it once per process instead.
        self._tools_validated = False

    @property
    def alias(self) -> str:
        return self._alias

    @property
    def description(self) -> str:
        return self._target.description

    async def schema(self, partition: TaskPartition) -> str:
        """Entity and property names, so the model can name them instead of guessing."""
        if self._schema is not None:
            return self._schema
        bearer_token = await self._token_provider.acquire(partition)
        if not bearer_token:
            raise ValueError("Fabric owner token is required")
        async with self._session_opener(self._target.endpoint, bearer_token) as session:
            await session.initialize()
            if not self._tools_validated:
                _validate_tools(await session.list_tools())
                self._tools_validated = True
            result = await session.call_tool("list_ontology_entity_types", {"includeProperties": True})
        self._schema = ontology_schema(result)
        return self._schema

    async def stream(
        self,
        partition: TaskPartition,
        question: str,
    ) -> AsyncGenerator[FabricQueryUpdate]:
        if not 3 <= len(question) <= MAX_ONTOLOGY_QUERY_CHARS:
            raise ValueError("ontology question must contain 3-10000 characters")
        yield FabricQueryUpdate(status="Checking Fabric connection", detail="Verifying the linked Fabric session.")
        bearer_token = await self._token_provider.acquire(partition)
        if not bearer_token:
            raise ValueError("Fabric owner token is required")
        yield FabricQueryUpdate(
            status=f"Connecting to {self._target.description}",
            detail="Opening one secure, read-only MCP session.",
        )
        async with self._session_opener(self._target.endpoint, bearer_token) as session:
            await session.initialize()
            if not self._tools_validated:
                yield FabricQueryUpdate(
                    status="Validating ontology tools",
                    detail="Confirming the approved two-tool ontology contract.",
                )
                _validate_tools(await session.list_tools())
                self._tools_validated = True
            yield FabricQueryUpdate(
                status=f"Querying {self._target.description}",
                detail="Running one read-only ontology MCP call.",
            )
            result = await session.call_tool(
                "search_ontology",
                {"naturalLanguageQuery": question, "naturalLanguageResponse": False},
            )
        yield FabricQueryUpdate(result=_ontology_result(result))


@asynccontextmanager
async def open_streamable_http_session(url: str, bearer_token: str) -> AsyncGenerator[OntologyMcpSession]:
    timeout = httpx.Timeout(60, connect=10)
    async with httpx.AsyncClient(
        headers={"Authorization": f"Bearer {bearer_token}"},
        follow_redirects=False,
        timeout=timeout,
    ) as http_client:
        async with streamable_http_client(url, http_client=http_client) as (read_stream, write_stream, _):
            async with ClientSession(read_stream, write_stream) as session:
                yield session


def _validate_tools(result: object) -> None:
    if isinstance(result, list):
        raw_tools: object = cast(list[object], result)
    elif isinstance(result, tuple):
        raw_tools = cast(tuple[object, ...], result)
    else:
        raw_tools = cast(object, getattr(result, "tools", None))
    actual: dict[str, dict[str, object]] = {}
    for raw_tool in _object_list(raw_tools):
        tool = _json_object(_model_dump(raw_tool))
        name = tool.get("name")
        description = tool.get("description")
        if not isinstance(name, str) or not isinstance(description, str):
            raise ValueError("ontology tool contract is malformed")
        actual[name] = {
            "description": description,
            "inputSchema": _normalize_schema(_json_object(tool.get("inputSchema"))),
        }
    expected = {
        name: {
            "description": definition["description"],
            "inputSchema": _normalize_schema(_json_object(definition["inputSchema"])),
        }
        for name, definition in EXPECTED_ONTOLOGY_TOOLS.items()
    }
    if actual != expected:
        raise ValueError("ontology tool contract does not match the accepted two-tool contract")


def _normalize_schema(schema: Mapping[str, object]) -> dict[str, object]:
    if schema.get("type") != "object":
        raise ValueError("ontology tool contract is malformed")
    raw_properties = _json_object(schema.get("properties"))
    properties: dict[str, dict[str, object]] = {}
    for name, value in raw_properties.items():
        definition = _json_object(value)
        properties[name] = {
            key: item for key, item in definition.items() if key in {"type", "minLength", "maxLength", "pattern"}
        }
    required_values = _object_list(schema.get("required", []))
    if not all(isinstance(name, str) for name in required_values):
        raise ValueError("ontology tool contract is malformed")
    return {"type": "object", "properties": properties, "required": [cast(str, name) for name in required_values]}


def _structured_payload(result: object, *, error_message: str) -> dict[str, object]:
    payload = _json_object(_model_dump(result))
    if payload.get("isError") is True:
        raise ValueError(error_message)
    raw_structured = payload.get("structuredContent")
    structured = _json_object(cast(object, raw_structured)) if isinstance(raw_structured, Mapping) else None
    if structured is None:
        for content in _object_list(payload.get("content", [])):
            content_value = _json_object(_model_dump(content))
            text = content_value.get("text")
            if isinstance(text, str):
                try:
                    structured = _json_object(cast(object, json.loads(text)))
                except (json.JSONDecodeError, ValueError):
                    continue
                break
    if structured is None:
        raise ValueError("ontology MCP response is malformed")
    return structured


def ontology_schema(result: object) -> str:
    structured = _structured_payload(result, error_message="ontology MCP schema listing returned an error")
    entities: list[str] = []
    compact_entities: list[str] = []
    for value in _object_list(structured.get("values", [])):
        record = _json_object(value)
        name = record.get("name")
        if not isinstance(name, str):
            continue
        enrichment = _enrichment(record.get("semanticEnrichment"))
        synonyms = [term for term in _string_list(enrichment.get("synonyms")) if term]
        header = f"{name} (also: {', '.join(synonyms)})" if synonyms else name
        described = _described_properties(record.get("properties"))
        properties = _property_names(record.get("properties"))
        series = _property_names(record.get("timeseriesProperties"))
        entity = f"{header}: {'; '.join(described)}" if described else header
        compact = f"{name}: {'; '.join(properties)}" if properties else name
        if series:
            entity = f"{entity}; time series {', '.join(series)}"
            compact = f"{compact}; time series {', '.join(series)}"
        description = enrichment.get("description")
        if isinstance(description, str) and description:
            entity = f"{entity} -- {description}"
        entities.append(entity)
        compact_entities.append(compact)
    schema = " | ".join(entities)
    if len(schema) > MAX_ONTOLOGY_SCHEMA_CHARS:
        schema = " | ".join(compact_entities)
    if len(schema) > MAX_ONTOLOGY_SCHEMA_CHARS:
        raise ValueError("ontology schema exceeds the description budget")
    return schema


def _enrichment(value: object) -> dict[str, object]:
    return _json_object(cast(object, value)) if isinstance(value, Mapping) else {}


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in cast(list[object], value) if isinstance(item, str)]


def _described_properties(value: object) -> list[str]:
    """Property descriptions carry the stored values, without which a filter is a guess."""
    described: list[str] = []
    for entry in _object_list(value or []):
        record = _json_object(entry)
        name = record.get("name")
        if not isinstance(name, str):
            continue
        value_type = record.get("valueType")
        typed_name = f"{name}:{value_type}" if isinstance(value_type, str) and value_type else name
        description = _enrichment(record.get("semanticEnrichment")).get("description")
        described.append(
            f"{typed_name} ({description})" if isinstance(description, str) and description else typed_name
        )
    return described


def _property_names(value: object) -> list[str]:
    names: list[str] = []
    for entry in _object_list(value or []):
        record = _json_object(entry)
        name = record.get("name")
        if isinstance(name, str):
            value_type = record.get("valueType")
            names.append(f"{name}:{value_type}" if isinstance(value_type, str) and value_type else name)
    return names


def _ontology_result(result: object) -> str:
    structured = _structured_payload(result, error_message="ontology MCP search returned an error")
    rows = structured.get("raw", structured)
    return _bounded_rows(_without_node_blobs(rows))


def _without_node_blobs(rows: object) -> object:
    # Each *_json column repeats the whole node, so it crowds real rows out of the size budget.
    if not isinstance(rows, Mapping):
        return rows
    table = _json_object(cast(object, rows))
    fields = table.get("Fields")
    values = table.get("Value")
    if not isinstance(fields, list) or not isinstance(values, list):
        return table
    names = cast(list[object], fields)
    keep = [index for index, name in enumerate(names) if not (isinstance(name, str) and name.endswith("_json"))]
    if len(keep) == len(names):
        return table
    trimmed: list[object] = []
    for row in cast(list[object], values):
        if isinstance(row, list):
            cells = cast(list[object], row)
            trimmed.append([cells[index] for index in keep if index < len(cells)])
        else:
            trimmed.append(row)
    return {**table, "Fields": [names[index] for index in keep], "Value": trimmed}


def _bounded_rows(rows: object) -> str:
    def serialize(value: object) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    serialized = serialize(rows)
    if len(serialized) <= MAX_ONTOLOGY_RESULT_CHARS:
        return serialized
    if not isinstance(rows, Mapping):
        raise ValueError(f"ontology MCP result exceeds {MAX_ONTOLOGY_RESULT_CHARS} characters")
    table = _json_object(cast(object, rows))
    values = table.get("Value")
    if not isinstance(values, list):
        raise ValueError(f"ontology MCP result exceeds {MAX_ONTOLOGY_RESULT_CHARS} characters")
    row_values = cast(list[object], values)
    total = len(row_values)
    kept = total // 2
    while kept > 0:
        candidate = {
            "Fields": table.get("Fields"),
            "Value": row_values[:kept],
            "returnedRows": kept,
            "totalRows": total,
            "truncated": True,
        }
        serialized = serialize(candidate)
        if len(serialized) <= MAX_ONTOLOGY_RESULT_CHARS:
            return serialized
        kept //= 2
    raise ValueError(f"ontology MCP result exceeds {MAX_ONTOLOGY_RESULT_CHARS} characters")


def _model_dump(value: object) -> object:
    model_dump = getattr(value, "model_dump", None)
    if not callable(model_dump):
        return value
    return model_dump(by_alias=True, exclude_none=True)


def _object_list(value: object) -> list[object]:
    if isinstance(value, list):
        return [*cast(list[object], value)]
    if isinstance(value, tuple):
        return [*cast(tuple[object, ...], value)]
    raise ValueError("ontology tool contract is malformed")


def _json_object(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("ontology MCP payload must be a JSON object")
    result: dict[str, object] = {}
    for key, item in cast(Mapping[object, object], value).items():
        if not isinstance(key, str):
            raise ValueError("ontology MCP payload has a non-string key")
        result[key] = item
    return result
