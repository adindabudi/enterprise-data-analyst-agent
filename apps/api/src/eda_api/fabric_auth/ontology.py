"""What the application still asks of the ontology's own MCP endpoint.

The analyst never discovers the ontology at run time. Its schema is read once,
by the operator's snapshot job (`scripts/build-fabric-schema-snapshot.py`, which
uses `list_entity_types` below), and pinned in the agent's instructions; every
answer is then a GQL or KQL query the agent writes itself. `search_ontology`,
which rewrote questions with a language model, is not used at all.

Two things remain here:

* `OntologyEndpointProbe`, the capacity status check. It opens the endpoint and
  runs nothing, because a paused capacity refuses the handshake itself.
* `ontology_schema`, the compact rendering the enrichment script checks its
  descriptions against.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import cast

from eda_api.fabric_ontology import OntologyTarget

from .capacity import CapacityMonitor, CapacityState
from .mcp_session import McpSession, SessionOpener, open_streamable_http_session

# Multi-entity operational sources need their units and grains, not just a compact list of names.
MAX_ONTOLOGY_SCHEMA_CHARS = 24_000
LIST_ENTITY_TYPES_TOOL = "list_ontology_entity_types"


class OntologyEndpointProbe:
    """Opens the ontology endpoint and runs nothing; a paused capacity refuses the handshake itself."""

    def __init__(
        self,
        target: OntologyTarget,
        *,
        session_opener: SessionOpener | None = None,
        capacity: CapacityMonitor | None = None,
    ) -> None:
        self._endpoint = target.endpoint
        self._session_opener = session_opener or open_streamable_http_session
        self._capacity = capacity

    async def probe_capacity(self, bearer_token: str) -> None:
        try:
            async with self._session_opener(self._endpoint, bearer_token) as session:
                await session.initialize()
        except Exception as error:
            if self._capacity is not None:
                self._capacity.observe_failure(error)
            raise
        # The endpoint answering the handshake is the capacity running.
        if self._capacity is not None:
            self._capacity.record(CapacityState.ACTIVE)


async def list_entity_types(session: McpSession) -> list[dict[str, object]]:
    """Every entity type with its properties, time-series properties and source bindings."""
    result = await session.call_tool(LIST_ENTITY_TYPES_TOOL, {"includeProperties": True})
    structured = _structured_payload(result, error_message="ontology MCP schema listing returned an error")
    return [_json_object(value) for value in _object_list(structured.get("values", []))]


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
