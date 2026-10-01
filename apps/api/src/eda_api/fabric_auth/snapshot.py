"""The source's schema, captured once and pinned in the agent's instructions.

Discovering an ontology at run time costs every question: a listing of the
entity types, one GQL introspection for the relationships that listing leaves
out, and a model turn for each. Measured with one model over the same questions,
discovering per question took 20.2 s, 4.4 model turns and 27k input tokens per
answer; the same agent with a snapshot in its instructions took 10.1 s, 2.2 turns
and under 5k tokens, and answered more of them correctly (12/12 against 11/12).

So the operator's snapshot job (`scripts/build-fabric-schema-snapshot.py`)
captures the schema when the ontology changes, never per question:

* entity types, properties and time-series bindings, from the ontology MCP's
  `list_ontology_entity_types`;
* relationship names and directions, which that listing omits, from one GQL
  introspection query;
* the stored values of every string property that holds only a few, which the
  listing also omits. Without them an agent filters on 'Cascade General' where
  the data says 'Lamna Healthcare Cascade General', and counts nothing.

The result is published to the runtime container and read once at startup. It
is deployment configuration, like the source's description: every user who has
linked Fabric sees it in the agent's instructions, while every query still runs
with that user's own token, so Fabric enforces their access on each read.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal, Protocol, Self, cast
from uuid import UUID

from azure.cosmos.exceptions import CosmosResourceNotFoundError
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from pydantic.alias_generators import to_camel

from eda_api.fabric_ontology import OntologyTarget

SNAPSHOT_RECORD_TYPE = "fabricSchemaSnapshot"
SNAPSHOT_SCHEMA_VERSION = 1
# A property with more distinct values than this holds names, identifiers or free text, not a category.
MAX_STORED_VALUES = 30
MAX_STORED_VALUE_CHARS = 120
MAX_DESCRIPTION_CHARS = 400
# The rendered snapshot rides along on every model call of every task, so it is kept deliberately small.
MAX_INSTRUCTIONS_CHARS = 24_000
RELATIONSHIP_QUERY = (
    "MATCH (a)-[e]->(b) RETURN labels(e) AS rel, labels(a) AS src, labels(b) AS dst, "
    "count(*) AS n GROUP BY rel, src, dst"
)

_ONTOLOGY_NAME = r"^[A-Za-z][A-Za-z0-9_-]{0,127}$"
_KQL_NAME = r"^[A-Za-z_][A-Za-z0-9_ .-]{0,127}$"
# Only a plain identifier can be written into a query without quoting rules the dialect may not share.
_PLAIN_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class SnapshotUnavailableError(RuntimeError):
    """No usable snapshot is published for the configured source."""


def snapshot_id(alias: str) -> str:
    return f"fabric-schema-snapshot:{alias}"


class _SnapshotModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True, alias_generator=to_camel)


class SnapshotProperty(_SnapshotModel):
    name: str = Field(pattern=_ONTOLOGY_NAME)
    value_type: str = Field(min_length=1, max_length=32)
    description: str | None = Field(default=None, max_length=MAX_DESCRIPTION_CHARS)
    key: bool = False
    # Every value stored in the property, when there are few; None when unknown or too many to list.
    stored_values: tuple[str, ...] | None = Field(default=None, max_length=MAX_STORED_VALUES)

    @model_validator(mode="after")
    def validate_values(self) -> Self:
        for value in self.stored_values or ():
            if not value or len(value) > MAX_STORED_VALUE_CHARS or _CONTROL.search(value):
                raise ValueError("a stored value must be 1-120 printable characters")
        return self


class SnapshotColumn(_SnapshotModel):
    column: str = Field(pattern=_KQL_NAME)
    property_name: str = Field(pattern=_ONTOLOGY_NAME)
    value_type: str | None = Field(default=None, max_length=32)


class SnapshotTimeSeries(_SnapshotModel):
    table: str = Field(pattern=_KQL_NAME)
    timestamp_column: str | None = Field(default=None, pattern=_KQL_NAME)
    # The columns that join a reading back to its entity's key properties.
    key_columns: tuple[SnapshotColumn, ...] = Field(default=(), max_length=16)
    value_columns: tuple[SnapshotColumn, ...] = Field(min_length=1, max_length=256)


class SnapshotEntity(_SnapshotModel):
    name: str = Field(pattern=_ONTOLOGY_NAME)
    description: str | None = Field(default=None, max_length=MAX_DESCRIPTION_CHARS)
    synonyms: tuple[str, ...] = Field(default=(), max_length=32)
    properties: tuple[SnapshotProperty, ...] = Field(min_length=1, max_length=512)
    time_series: tuple[SnapshotTimeSeries, ...] = Field(default=(), max_length=8)


class SnapshotRelationship(_SnapshotModel):
    name: str = Field(pattern=_ONTOLOGY_NAME)
    source: str = Field(pattern=_ONTOLOGY_NAME)
    target: str = Field(pattern=_ONTOLOGY_NAME)
    count: int | None = Field(default=None, ge=0)


class SchemaSnapshot(_SnapshotModel):
    id: str = Field(min_length=1, max_length=128)
    record_type: Literal["fabricSchemaSnapshot"] = SNAPSHOT_RECORD_TYPE
    schema_version: Literal[1] = SNAPSHOT_SCHEMA_VERSION
    alias: str = Field(pattern=r"^[a-z][a-z0-9-]{1,39}$")
    workspace_id: UUID
    ontology_id: UUID
    graph_model_id: UUID
    generated_at: datetime
    entities: tuple[SnapshotEntity, ...] = Field(min_length=1, max_length=512)
    relationships: tuple[SnapshotRelationship, ...] = Field(default=(), max_length=2048)

    @model_validator(mode="after")
    def validate_snapshot(self) -> Self:
        if self.id != snapshot_id(self.alias):
            raise ValueError("snapshot id must match its alias")
        names = [entity.name for entity in self.entities]
        if len(set(names)) != len(names):
            raise ValueError("snapshot contains duplicate entity names")
        if len(render_snapshot(self, description="-", timeseries=True)) > MAX_INSTRUCTIONS_CHARS:
            raise ValueError(f"the rendered snapshot exceeds {MAX_INSTRUCTIONS_CHARS} characters")
        return self

    def binds(self, alias: str, target: OntologyTarget) -> bool:
        """A snapshot describes exactly one configured source; another source's schema is worse than none."""
        return (
            self.alias == alias
            and self.workspace_id == target.workspace_id
            and self.ontology_id == target.ontology_id
            and (target.graph_model_id is None or self.graph_model_id == target.graph_model_id)
        )

    @property
    def has_time_series(self) -> bool:
        return any(entity.time_series for entity in self.entities)


def _clip(value: object, limit: int = MAX_DESCRIPTION_CHARS) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(_CONTROL.sub(" ", value).split())
    return text[:limit] or None


def _mapping(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in cast(Mapping[object, object], value).items()}


def _items(value: object) -> list[object]:
    return [*cast(list[object], value)] if isinstance(value, list) else []


def stored_values_query(entity: str, prop: str, *, max_values: int = MAX_STORED_VALUES) -> str | None:
    """One grouped read of a string property; one extra row says whether the values were all listed."""
    if not (_PLAIN_IDENTIFIER.fullmatch(entity) and _PLAIN_IDENTIFIER.fullmatch(prop)):
        return None
    return (
        f"MATCH (n:{entity}) LET v = n.{prop} RETURN v, count(*) AS c GROUP BY v ORDER BY c DESC LIMIT {max_values + 1}"
    )


def stored_values_from_rows(rows: list[object], *, max_values: int = MAX_STORED_VALUES) -> tuple[str, ...] | None:
    """All stored values, or None when there are too many or any could not be written down safely."""
    if len(rows) > max_values:
        return None
    values: list[str] = []
    for row in rows:
        value = _mapping(row).get("v")
        if value is None:
            continue
        if not isinstance(value, str) or not value or len(value) > MAX_STORED_VALUE_CHARS or _CONTROL.search(value):
            return None
        values.append(value)
    return tuple(sorted(set(values)))


def value_candidates(listing: list[dict[str, object]]) -> list[tuple[str, str]]:
    """String properties of graph entities, whose stored values the snapshot job reads."""
    candidates: list[tuple[str, str]] = []
    for entity in listing:
        name = entity.get("name")
        if not isinstance(name, str):
            continue
        for raw in _items(entity.get("properties")):
            prop = _mapping(raw)
            prop_name = prop.get("name")
            if isinstance(prop_name, str) and prop.get("valueType") == "String":
                candidates.append((name, prop_name))
    return candidates


def relationships_from_rows(rows: list[object]) -> tuple[SnapshotRelationship, ...]:
    seen: dict[tuple[str, str, str], SnapshotRelationship] = {}
    for row in rows:
        record = _mapping(row)
        names = [_first_label(record.get(key)) for key in ("rel", "src", "dst")]
        if not all(names):
            continue
        count = record.get("n")
        edge = SnapshotRelationship(
            name=names[0],
            source=names[1],
            target=names[2],
            count=int(count) if isinstance(count, int | str) and str(count).isdigit() else None,
        )
        seen.setdefault((edge.name, edge.source, edge.target), edge)
    return tuple(sorted(seen.values(), key=lambda edge: (edge.source, edge.name, edge.target)))


def _first_label(value: object) -> str:
    if isinstance(value, str):
        return value
    labels = _items(value)
    return labels[0] if labels and isinstance(labels[0], str) else ""


def entity_from_listing(
    entity: Mapping[str, object],
    stored_values: Mapping[tuple[str, str], tuple[str, ...] | None],
) -> SnapshotEntity:
    name = cast(str, entity.get("name"))
    key_ids = {item for item in _items(entity.get("entityIdParts")) if isinstance(item, str)}
    names_by_id: dict[str, str] = {}
    properties: list[SnapshotProperty] = []
    for raw in _items(entity.get("properties")):
        prop = _mapping(raw)
        prop_id, prop_name, value_type = prop.get("id"), prop.get("name"), prop.get("valueType")
        if not isinstance(prop_name, str) or not isinstance(value_type, str):
            continue
        if isinstance(prop_id, str):
            names_by_id[prop_id] = prop_name
        properties.append(
            SnapshotProperty(
                name=prop_name,
                value_type=value_type,
                description=_clip(_mapping(prop.get("semanticEnrichment")).get("description")),
                key=prop_id in key_ids,
                stored_values=stored_values.get((name, prop_name)),
            )
        )
    series: dict[str, tuple[str, str]] = {}
    for raw in _items(entity.get("timeseriesProperties")):
        prop = _mapping(raw)
        prop_id, prop_name, value_type = prop.get("id"), prop.get("name"), prop.get("valueType")
        if isinstance(prop_id, str) and isinstance(prop_name, str):
            series[prop_id] = (prop_name, value_type if isinstance(value_type, str) else "")
    enrichment = _mapping(entity.get("semanticEnrichment"))
    synonyms = tuple(
        clipped for clipped in (_clip(term, 64) for term in _items(enrichment.get("synonyms"))) if clipped
    )[:32]
    return SnapshotEntity(
        name=name,
        description=_clip(enrichment.get("description")),
        synonyms=synonyms,
        properties=tuple(properties),
        time_series=_time_series(entity, key_ids, names_by_id, series),
    )


def _time_series(
    entity: Mapping[str, object],
    key_ids: set[str],
    names_by_id: Mapping[str, str],
    series: Mapping[str, tuple[str, str]],
) -> tuple[SnapshotTimeSeries, ...]:
    """Where each time-series property is read from, and which column joins it back to the entity."""
    bound: list[SnapshotTimeSeries] = []
    for raw in _items(entity.get("mappings")):
        configuration = _mapping(_mapping(raw).get("mappingConfiguration"))
        if configuration.get("mappingType") != "TimeSeries":
            continue
        table = _mapping(configuration.get("sourceTableProperties")).get("sourceTableName")
        if not isinstance(table, str):
            continue
        timestamp = configuration.get("timestampColumnName")
        keys: list[SnapshotColumn] = []
        values: list[SnapshotColumn] = []
        for binding_raw in _items(configuration.get("propertyBindings")):
            binding = _mapping(binding_raw)
            column, target = binding.get("sourceColumnName"), binding.get("targetPropertyId")
            if not isinstance(column, str) or not isinstance(target, str) or column == timestamp:
                continue
            if target in key_ids and target in names_by_id:
                keys.append(SnapshotColumn(column=column, property_name=names_by_id[target]))
            elif target in series:
                prop_name, value_type = series[target]
                values.append(SnapshotColumn(column=column, property_name=prop_name, value_type=value_type or None))
        if values:
            bound.append(
                SnapshotTimeSeries(
                    table=table,
                    timestamp_column=timestamp if isinstance(timestamp, str) else None,
                    key_columns=tuple(keys),
                    value_columns=tuple(values),
                )
            )
    return tuple(bound)


def build_snapshot(
    *,
    alias: str,
    target: OntologyTarget,
    graph_model_id: UUID,
    listing: list[dict[str, object]],
    relationship_rows: list[object],
    stored_values: Mapping[tuple[str, str], tuple[str, ...] | None],
    generated_at: datetime,
) -> SchemaSnapshot:
    entities = tuple(
        entity_from_listing(entity, stored_values) for entity in listing if isinstance(entity.get("name"), str)
    )
    return SchemaSnapshot(
        id=snapshot_id(alias),
        alias=alias,
        workspace_id=target.workspace_id,
        ontology_id=target.ontology_id,
        graph_model_id=graph_model_id,
        generated_at=generated_at,
        entities=tuple(sorted(entities, key=lambda entity: entity.name)),
        relationships=relationships_from_rows(relationship_rows),
    )


def _quoted(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _render_property(prop: SnapshotProperty) -> str:
    text = f"{prop.name} {prop.value_type}"
    if prop.key:
        text += " key"
    if prop.stored_values:
        text += " = " + " | ".join(_quoted(value) for value in prop.stored_values)
    if prop.description:
        text += f" ({prop.description})"
    return text


def _render_time_series(entity: SnapshotEntity, series: SnapshotTimeSeries) -> str:
    columns = ", ".join(
        f"{column.column} {column.value_type}" if column.value_type else column.column
        for column in series.value_columns
    )
    text = f"  time series (not in the graph): Eventhouse table {series.table}"
    if series.timestamp_column:
        text += f", time column {series.timestamp_column}"
    text += f"; columns {columns}"
    joins = " and ".join(
        f"{series.table}.{column.column} = {entity.name}.{column.property_name}" for column in series.key_columns
    )
    if joins:
        text += f"; join {joins}"
    return text


def render_snapshot(snapshot: SchemaSnapshot, *, description: str, timeseries: bool) -> str:
    """The snapshot as the agent reads it, plus what it says about routing a question."""
    lines = [
        f"Fabric source '{snapshot.alias}': {description}",
        f"Its schema below was captured from the ontology on {snapshot.generated_at:%Y-%m-%d} and is complete. "
        "Write queries from it directly; do not run a query to discover labels, relationships or values it "
        "already lists. Node labels are the entity names. A value list after a property holds every value "
        "stored there: filter on those values exactly as written.",
        "Entities (property type, key, stored values, meaning):",
    ]
    for entity in snapshot.entities:
        header = f"- {entity.name}"
        if entity.synonyms:
            header += f" (also: {', '.join(entity.synonyms)})"
        header += ": " + "; ".join(_render_property(prop) for prop in entity.properties)
        if entity.description:
            header += f" -- {entity.description}"
        lines.append(header)
        lines.extend(_render_time_series(entity, series) for series in entity.time_series)
    if snapshot.relationships:
        edges = "; ".join(f"(:{edge.source})-[:{edge.name}]->(:{edge.target})" for edge in snapshot.relationships)
        lines.append(f"Relationships (direction matters): {edges}")
    lines.append(
        "Use query_graph (GQL) for counts, totals, ratios, thresholds, rankings and per-group breakdowns over "
        "entities and relationships. Answer a multi-part question with as few queries as possible, and issue "
        "independent queries together. A property without a value list holds many values, such as names, "
        "identifiers or free text: match it exactly as the user wrote it, and if nothing matches, say so "
        "rather than guessing a similar value."
    )
    if snapshot.has_time_series:
        lines.append(
            "Time-series columns are only in their Eventhouse table: read them with query_timeseries (KQL), "
            "aggregate there, then join to the graph on the listed key column when the answer needs entity "
            "properties."
            if timeseries
            else "Time-series readings cannot be read in this deployment; say so when a request needs them."
        )
    return "\n".join(lines)


class SnapshotContainer(Protocol):
    async def read_item(self, item: str, partition_key: str) -> Mapping[str, Any]: ...


async def read_snapshot(container: SnapshotContainer, alias: str, target: OntologyTarget) -> SchemaSnapshot:
    """The published snapshot for the configured source, or the reason there is none to use."""
    record_id = snapshot_id(alias)
    try:
        document = await container.read_item(item=record_id, partition_key=record_id)
    except CosmosResourceNotFoundError:
        raise SnapshotUnavailableError(
            f"no schema snapshot is published for '{alias}'; run scripts/build-fabric-schema-snapshot.py --publish"
        ) from None
    try:
        snapshot = SchemaSnapshot.model_validate(
            {key: value for key, value in document.items() if not key.startswith("_")}
        )
    except ValidationError as error:
        raise SnapshotUnavailableError(
            f"the published schema snapshot for '{alias}' is invalid ({error.error_count()} errors)"
        ) from None
    if not snapshot.binds(alias, target):
        raise SnapshotUnavailableError(f"the published schema snapshot for '{alias}' describes a different source")
    return snapshot
