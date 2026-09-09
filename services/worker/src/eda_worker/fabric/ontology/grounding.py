from __future__ import annotations

import json
import re
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import cast

MAX_ENTITIES = 512
MAX_GROUNDING_BYTES = 256 * 1024
MAX_GUIDE_BYTES = 16 * 1024
MAX_SCHEMA_SUMMARY_CHARS = 6_000
MAX_AUTOMATIC_ROUTING_TERMS = 256
_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,127}$")
_VALUE_TYPES = frozenset({"BigInt", "Boolean", "DateTime", "Double", "Int", "Long", "String"})

type NormalizedProperty = dict[str, str]
type NormalizedEntity = dict[str, list[NormalizedProperty] | list[str] | str]
type NormalizedSchema = dict[str, list[NormalizedEntity]]
type GroundingCacheKey = tuple[str, str, str, str]


@dataclass(frozen=True)
class GroundingCacheEntry:
    grounding: NormalizedSchema
    digest: str


class OntologyGroundingCache:
    def __init__(self) -> None:
        self._entries: dict[GroundingCacheKey, GroundingCacheEntry] = {}

    def get(self, key: GroundingCacheKey) -> GroundingCacheEntry | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        return GroundingCacheEntry(grounding=deepcopy(entry.grounding), digest=entry.digest)

    def put(self, key: GroundingCacheKey, entry: GroundingCacheEntry) -> None:
        self._entries[key] = GroundingCacheEntry(grounding=deepcopy(entry.grounding), digest=entry.digest)

    def clear_task(self, owner_partition_key: str, task_id: str) -> None:
        for key in tuple(self._entries):
            if key[:2] == (owner_partition_key, task_id):
                del self._entries[key]


def normalize_ontology_schema(raw: Mapping[str, object]) -> NormalizedSchema:
    values = object_list(raw.get("values"))
    if not values or len(values) > MAX_ENTITIES:
        raise ValueError("ontology schema must contain 1-512 entities")

    entities = [_normalize_entity(json_object(value)) for value in values]
    names = [entity["name"] for entity in entities]
    if len(set(names)) != len(names):
        raise ValueError("ontology schema contains duplicate entity names")
    normalized: NormalizedSchema = {"entities": sorted(entities, key=lambda entity: entity["name"])}
    if len(json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode()) > MAX_GROUNDING_BYTES:
        raise ValueError("ontology schema exceeds 256 KiB safe grounding limit")
    return normalized


def build_source_guide(
    *,
    alias: str,
    description: str,
    normalized_schema: Mapping[str, object],
    routing_terms: tuple[str, ...],
) -> dict[str, str | list[str]]:
    if not alias or not description:
        raise ValueError("ontology source guide requires alias and description")
    automatic_terms = _automatic_routing_terms(normalized_schema)
    if len(automatic_terms) <= MAX_AUTOMATIC_ROUTING_TERMS:
        guide: dict[str, str | list[str]] = {
            "alias": alias,
            "description": description,
            "routingMode": "automatic",
            "routingTerms": automatic_terms,
        }
    else:
        if not routing_terms:
            raise ValueError("ontology source guide requires curated routing terms")
        guide = {
            "alias": alias,
            "description": description,
            "routingMode": "curated",
            "routingTerms": list(routing_terms),
        }
    if len(json.dumps(guide, sort_keys=True, separators=(",", ":")).encode()) > MAX_GUIDE_BYTES:
        raise ValueError("ontology source guide exceeds 16 KiB")
    return guide


_ENTITY_SEPARATOR = " | "
# The notice replaces the entities that did not fit, so its own room has to be held back first.
_OMISSION_RESERVE = 80


def describe_grounding(normalized_schema: Mapping[str, object]) -> str:
    """Renders the grounding into the result itself, so a schema call answers without a second read."""
    described = [_describe_entity(json_object(value)) for value in object_list(normalized_schema.get("entities"))]
    included: list[str] = []
    remaining = MAX_SCHEMA_SUMMARY_CHARS - _OMISSION_RESERVE
    for entity in described:
        if len(entity) + len(_ENTITY_SEPARATOR) > remaining:
            break
        included.append(entity)
        remaining -= len(entity) + len(_ENTITY_SEPARATOR)
    omitted = len(described) - len(included)
    if omitted:
        included.append(f"{omitted} entities omitted here; read the artifact for the rest")
    return _ENTITY_SEPARATOR.join(included)


def _describe_entity(entity: Mapping[str, object]) -> str:
    name = string_value(entity.get("name"), "normalized entity name")
    synonyms = [string_value(term, "normalized synonym") for term in object_list(entity.get("synonyms") or [])]
    described = f"{name} (also: {', '.join(synonyms)})" if synonyms else name
    properties = [_describe_property(json_object(value)) for value in object_list(entity.get("properties") or [])]
    if properties:
        described = f"{described}: {'; '.join(properties)}"
    series = [_describe_property(json_object(value)) for value in object_list(entity.get("timeSeriesProperties") or [])]
    if series:
        described = f"{described}; time series {', '.join(series)}"
    description = entity.get("description")
    if isinstance(description, str) and description:
        described = f"{described} -- {description}"
    return described


def _describe_property(property_value: Mapping[str, object]) -> str:
    name = string_value(property_value.get("name"), "normalized property name")
    value_type = string_value(property_value.get("valueType"), "normalized property value type")
    description = property_value.get("description")
    if isinstance(description, str) and description:
        return f"{name} {value_type} ({description})"
    return f"{name} {value_type}"


def _automatic_routing_terms(normalized_schema: Mapping[str, object]) -> list[str]:
    terms: set[str] = set()
    for entity_value in object_list(normalized_schema.get("entities")):
        entity = json_object(entity_value)
        name = string_value(entity.get("name"), "normalized entity name")
        terms.add(name)
        for key_property in object_list(entity.get("keyProperties")):
            terms.add(string_value(key_property, "normalized key property"))
        for time_series_property in object_list(entity.get("timeSeriesProperties")):
            property_value = json_object(time_series_property)
            terms.add(string_value(property_value.get("name"), "normalized time-series property name"))
    return sorted(terms)


def _normalize_entity(entity: Mapping[str, object]) -> NormalizedEntity:
    name = string_value(entity.get("name"), "entity name")
    validate_name(name, "entity name")
    raw_properties = object_list(entity.get("properties"))
    if not raw_properties:
        raise ValueError("ontology entity must contain properties")

    properties_by_id: dict[str, NormalizedProperty] = {}
    property_names: set[str] = set()
    for raw_property in raw_properties:
        property_value = json_object(raw_property)
        property_id = string_value(property_value.get("id"), "property ID")
        property_name = string_value(property_value.get("name"), "property name")
        value_type = string_value(property_value.get("valueType"), "property value type")
        validate_name(property_name, "property name")
        if value_type not in _VALUE_TYPES:
            raise ValueError("ontology property has an unknown value type")
        if property_id in properties_by_id or property_name in property_names:
            raise ValueError("ontology entity contains duplicate properties")
        property_names.add(property_name)
        properties_by_id[property_id] = {
            "name": property_name,
            "valueType": value_type,
            **_curated_description(property_value.get("semanticEnrichment")),
        }

    key_ids = object_list(entity.get("entityIdParts"))
    key_properties: list[str] = []
    for key_id in key_ids:
        identifier = string_value(key_id, "entity key ID")
        property_value = properties_by_id.get(identifier)
        if property_value is None:
            raise ValueError("ontology entity key references a missing property")
        key_properties.append(property_value["name"])

    return {
        "name": name,
        **_curated_entity_meaning(entity.get("semanticEnrichment")),
        "keyProperties": sorted(key_properties),
        "properties": sorted(properties_by_id.values(), key=lambda property_value: property_value["name"]),
        "timeSeriesProperties": _normalize_time_series_properties(entity.get("timeseriesProperties")),
    }


def _normalize_time_series_properties(value: object) -> list[NormalizedProperty]:
    properties: list[NormalizedProperty] = []
    names: set[str] = set()
    for raw_property in object_list(value):
        property_value = json_object(raw_property)
        name = string_value(property_value.get("name"), "time-series property name")
        value_type = string_value(property_value.get("valueType"), "time-series property value type")
        validate_name(name, "time-series property name")
        if value_type not in _VALUE_TYPES:
            raise ValueError("ontology time-series property has an unknown value type")
        if name in names:
            raise ValueError("ontology entity contains duplicate time-series properties")
        names.add(name)
        properties.append(
            {
                "name": name,
                "valueType": value_type,
                **_curated_description(property_value.get("semanticEnrichment")),
            }
        )
    return sorted(properties, key=lambda property_value: property_value["name"])


def validate_name(value: str, label: str) -> None:
    if not _NAME_PATTERN.fullmatch(value):
        raise ValueError(f"ontology {label} is malformed")


_TOPOLOGY_IDENTIFIER = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.IGNORECASE)
_TOPOLOGY_LINK = re.compile(r"https?://\S+", re.IGNORECASE)


def _without_topology(value: str) -> str:
    """Curated text is the one field a human writes freely, so it is the one that can reopen this."""
    return _TOPOLOGY_LINK.sub("[link]", _TOPOLOGY_IDENTIFIER.sub("[id]", value))


def _curated_description(value: object) -> dict[str, str]:
    """Property descriptions are where an ontology writes down the values it actually stores."""
    if value is None:
        return {}
    description = json_object(value).get("description")
    if description is None:
        return {}
    return {"description": _without_topology(string_value(description, "enrichment description"))}


def _curated_entity_meaning(value: object) -> dict[str, str | list[str]]:
    if value is None:
        return {}
    enrichment = json_object(value)
    curated: dict[str, str | list[str]] = {**_curated_description(enrichment)}
    synonyms = enrichment.get("synonyms")
    if synonyms is not None:
        terms = sorted({_without_topology(string_value(term, "entity synonym")) for term in object_list(synonyms)})
        if terms:
            curated["synonyms"] = terms
    return curated


def string_value(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"ontology {label} must be a nonempty string")
    return value


def object_list(value: object) -> list[object]:
    if not isinstance(value, list):
        raise ValueError("ontology schema has an invalid array field")
    return [*cast(list[object], value)]


def json_object(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("ontology schema has an invalid object field")
    raw_mapping = cast(Mapping[object, object], value)
    normalized: dict[str, object] = {}
    for key, item in raw_mapping.items():
        if not isinstance(key, str):
            raise ValueError("ontology schema has a non-string key")
        normalized[key] = item
    return normalized
