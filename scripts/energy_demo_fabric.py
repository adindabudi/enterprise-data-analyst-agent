"""Compile the synthetic energy contract into Fabric artifacts. This command never calls Azure."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import cast
from uuid import UUID, uuid5

SCHEMA_ROOT = "https://developer.microsoft.com/json-schemas/fabric/item/ontology"
NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,127}$")
VALUE_TYPES = frozenset({"String", "BigInt", "Double", "Boolean", "DateTime"})
NAMESPACE = UUID("6479e888-6bb7-53fc-960c-847f81d78c21")


@dataclass(frozen=True)
class Column:
    name: str
    value_type: str
    description: str
    nullable: bool


@dataclass(frozen=True)
class Table:
    name: str
    key: str
    description: str
    synonyms: tuple[str, ...]
    columns: tuple[Column, ...]
    ontology: bool


def object_value(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("expected a JSON object")
    return cast(dict[str, object], value)


def list_value(value: object) -> list[object]:
    if not isinstance(value, list):
        raise ValueError("expected a JSON array")
    return cast(list[object], value)


def text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("expected a nonempty string")
    return value


def identifier(value: object) -> str:
    name = text(value)
    if not NAME.fullmatch(name):
        raise ValueError(f"invalid Fabric identifier: {name}")
    return name


def parse_tables(schema: dict[str, object]) -> list[Table]:
    tables: list[Table] = []
    for raw in list_value(schema["tables"]):
        table = object_value(raw)
        columns: list[Column] = []
        for raw_column in list_value(table["columns"]):
            column = object_value(raw_column)
            value_type = text(column["type"])
            nullable = column["nullable"]
            if value_type not in VALUE_TYPES or not isinstance(nullable, bool):
                raise ValueError("invalid column type or nullability")
            columns.append(Column(identifier(column["name"]), value_type, text(column["description"]), nullable))
        key = identifier(table["key"])
        names = [column.name for column in columns]
        if len(names) != len(set(names)) or key not in names:
            raise ValueError("duplicate columns or missing primary key")
        key_column = next(column for column in columns if column.name == key)
        if key_column.nullable:
            raise ValueError("entity keys cannot be nullable")
        enabled = table.get("ontology", True)
        if not isinstance(enabled, bool):
            raise ValueError("ontology must be Boolean")
        tables.append(
            Table(
                identifier(table["name"]),
                key,
                text(table["description"]),
                tuple(text(value) for value in list_value(table.get("synonyms", []))),
                tuple(columns),
                enabled,
            )
        )
    if not tables or len({table.name.casefold() for table in tables}) != len(tables):
        raise ValueError("empty or duplicate table names")
    return tables


def stable_id(kind: str, name: str) -> str:
    digest = hashlib.sha256(f"energy-indonesia-v1:{kind}:{name}".encode()).digest()
    return str(int.from_bytes(digest[:8], "big") & ((1 << 63) - 1))


def property_id(table: str, column: str) -> str:
    return stable_id("property", f"{table}.{column}")


def part(path: str, body: object) -> dict[str, str]:
    # Fabric's polymorphic deserializer requires discriminator fields such as sourceType first.
    payload = json.dumps(body, separators=(",", ":"), ensure_ascii=True).encode()
    return {"path": path, "payload": base64.b64encode(payload).decode(), "payloadType": "InlineBase64"}


def table_ref(table: str, workspace: str, lakehouse: str) -> dict[str, str]:
    return {
        "sourceType": "LakehouseTable",
        "workspaceId": workspace,
        "itemId": lakehouse,
        "sourceTableName": table,
        "sourceSchema": "dbo",
    }


def compile_ontology(
    schema: dict[str, object],
    *,
    workspace: str | None = None,
    lakehouse: str | None = None,
    table_names: dict[str, str] | None = None,
) -> tuple[dict[str, object], dict[str, object]]:
    if bool(workspace) != bool(lakehouse):
        raise ValueError("provide both workspace and lakehouse UUIDs, or neither for an unbound blueprint")
    if workspace and lakehouse:
        workspace, lakehouse = str(UUID(workspace)), str(UUID(lakehouse))
        if int(UUID(workspace)) == 0 or int(UUID(lakehouse)) == 0:
            raise ValueError("nil UUID is not a deployment target")
    tables = parse_tables(schema)
    by_name = {table.name: table for table in tables}
    if table_names is not None and set(table_names) != set(by_name):
        raise ValueError("physical table-name mapping must cover exactly the contract tables")
    physical_names = {
        table.name: identifier(table_names[table.name]) if table_names is not None else table.name for table in tables
    }
    if len({name.casefold() for name in physical_names.values()}) != len(physical_names):
        raise ValueError("physical table-name mapping contains duplicate targets")
    entity_tables = [table for table in tables if table.ontology]
    entity_enrichment: dict[str, object] = {}
    relationship_enrichment: dict[str, object] = {}
    parts = [part("definition.json", {})]
    ids: set[str] = set()

    def claim(value: str) -> str:
        if value in ids:
            raise ValueError(f"generated identifier collision: {value}")
        ids.add(value)
        return value

    for table in entity_tables:
        entity_id = claim(stable_id("entity", table.name))
        properties: list[dict[str, object]] = [
            {
                "id": claim(property_id(table.name, column.name)),
                "name": column.name,
                "valueType": column.value_type,
            }
            for column in table.columns
        ]
        body: dict[str, object] = {
            "$schema": f"{SCHEMA_ROOT}/entityType/1.0.0/schema.json",
            "id": entity_id,
            "namespace": "usertypes",
            "name": table.name,
            "namespaceType": "Custom",
            "visibility": "Visible",
            "entityIdParts": [property_id(table.name, table.key)],
            "displayNamePropertyId": property_id(
                table.name,
                next(
                    (column.name for column in table.columns if column.name in (f"{table.name}Name", "Name", "Title")),
                    table.key,
                ),
            ),
            "properties": properties,
            "timeseriesProperties": [],
            "semanticEnrichment": {
                "description": table.description,
                "synonyms": list(table.synonyms),
                "customAttributes": {"dataClass": "fictional-synthetic-demo", "primaryKey": table.key},
            },
        }
        for property_value, column in zip(properties, table.columns, strict=True):
            property_value["semanticEnrichment"] = {"description": column.description}
        parts.append(part(f"EntityTypes/{entity_id}/definition.json", body))
        entity_enrichment[table.name] = {
            "description": table.description,
            "synonyms": list(table.synonyms),
            "properties": {column.name: {"description": column.description} for column in table.columns},
        }
        if workspace and lakehouse:
            binding_id = str(uuid5(NAMESPACE, f"binding:{table.name}"))
            binding = {
                "$schema": f"{SCHEMA_ROOT}/dataBinding/1.0.0/schema.json",
                "id": binding_id,
                "dataBindingConfiguration": {
                    "dataBindingType": "NonTimeSeries",
                    "propertyBindings": [
                        {"sourceColumnName": column.name, "targetPropertyId": property_id(table.name, column.name)}
                        for column in table.columns
                    ],
                    "sourceTableProperties": table_ref(physical_names[table.name], workspace, lakehouse),
                },
            }
            parts.append(part(f"EntityTypes/{entity_id}/DataBindings/{binding_id}.json", binding))

    relation_names: set[str] = set()
    for raw in list_value(schema["relationships"]):
        relation = object_value(raw)
        name = identifier(relation["name"])
        if name in relation_names:
            raise ValueError(f"duplicate relationship name: {name}")
        relation_names.add(name)
        source = by_name[identifier(relation["source"])]
        target = by_name[identifier(relation["target"])]
        bridge = by_name[identifier(relation["table"])]
        source_column = identifier(relation["source_key"])
        target_column = identifier(relation["target_key"])
        columns = {column.name: column for column in bridge.columns}
        if not source.ontology or not target.ontology:
            raise ValueError("relationships must connect ontology entities")
        for name_column, endpoint in ((source_column, source), (target_column, target)):
            endpoint_type = next(column.value_type for column in endpoint.columns if column.name == endpoint.key)
            if name_column not in columns or columns[name_column].value_type != endpoint_type:
                raise ValueError("relationship binding column is missing or has an incompatible key type")
        relation_id = claim(stable_id("relationship", name))
        definition = {
            "$schema": f"{SCHEMA_ROOT}/relationshipType/1.0.0/schema.json",
            "namespace": "usertypes",
            "id": relation_id,
            "name": name,
            "namespaceType": "Custom",
            "source": {"entityTypeId": stable_id("entity", source.name)},
            "target": {"entityTypeId": stable_id("entity", target.name)},
            "semanticEnrichment": {
                "description": text(relation["description"]),
                "customAttributes": {"cardinality": text(relation["cardinality"])},
            },
        }
        parts.append(part(f"RelationshipTypes/{relation_id}/definition.json", definition))
        relationship_enrichment[name] = {
            "description": text(relation["description"]),
            "customAttributes": {"cardinality": text(relation["cardinality"])},
        }
        if workspace and lakehouse:
            contextualization_id = str(uuid5(NAMESPACE, f"relationship-binding:{name}"))
            contextualization = {
                "$schema": f"{SCHEMA_ROOT}/contextualization/1.0.0/schema.json",
                "id": contextualization_id,
                "dataBindingTable": table_ref(physical_names[bridge.name], workspace, lakehouse),
                "sourceKeyRefBindings": [
                    {"sourceColumnName": source_column, "targetPropertyId": property_id(source.name, source.key)}
                ],
                "targetKeyRefBindings": [
                    {"sourceColumnName": target_column, "targetPropertyId": property_id(target.name, target.key)}
                ],
            }
            parts.append(
                part(
                    f"RelationshipTypes/{relation_id}/Contextualizations/{contextualization_id}.json",
                    contextualization,
                )
            )
    return {"definition": {"parts": parts}}, {
        "entityTypes": entity_enrichment,
        "relationshipTypes": relationship_enrichment,
    }


def notebook(
    schema: dict[str, object], *, workspace: str | None = None, lakehouse: str | None = None
) -> dict[str, object]:
    """An explicit-schema, create-only loader. Evaluation material is never read or uploaded."""
    parse_tables(schema)
    serialized = json.dumps(schema, ensure_ascii=True)
    code = """import json
from pathlib import Path
import notebookutils
from pyspark.sql import functions as F
from pyspark.sql.types import (
    StructType, StructField, StringType, LongType, DoubleType, BooleanType, TimestampType
)

# Attach the NEW dedicated lakehouse before running. Never attach Lamna or Centoso.
APPLY = False
EXPECTED_LAKEHOUSE_NAME = "IndonesiaEnergyDemoLH"
ROOT = Path("/lakehouse/default/Files/energy-demo")
"""
    code += f"CONTRACT = json.loads({serialized!r})\n"
    code += f"EXPECTED_WORKSPACE_ID = {workspace!r}\nEXPECTED_LAKEHOUSE_ID = {lakehouse!r}\n"
    code += """
TYPE_MAP = {
    "String": StringType(), "BigInt": LongType(), "Double": DoubleType(),
    "Boolean": BooleanType(), "DateTime": TimestampType()
}
spark.conf.set("spark.sql.session.timeZone", "UTC")
context = notebookutils.runtime.context
if context.get("defaultLakehouseName") != EXPECTED_LAKEHOUSE_NAME:
    raise ValueError("Attach the dedicated IndonesiaEnergyDemoLH; refusing an unexpected lakehouse.")
if EXPECTED_LAKEHOUSE_ID and context.get("defaultLakehouseId") != EXPECTED_LAKEHOUSE_ID:
    raise ValueError("The default lakehouse UUID differs from the compiled binding target.")
if EXPECTED_WORKSPACE_ID and context.get("defaultLakehouseWorkspaceId") != EXPECTED_WORKSPACE_ID:
    raise ValueError("The default lakehouse workspace differs from the compiled binding target.")
manifest = json.loads((ROOT / "manifest.json").read_text())
frames = {}
for table in CONTRACT["tables"]:
    name = table["name"]
    if spark.catalog.tableExists(f"dbo.{name}"):
        raise ValueError(f"Table dbo.{name} exists. Use a fresh lakehouse; this loader never overwrites.")
    columns = table["columns"]
    shape = StructType([
        StructField(c["name"], TYPE_MAP[c["type"]], c["nullable"]) for c in columns
    ])
    frame = (
        spark.read.schema(shape).option("header", True).option("mode", "FAILFAST")
        .option("enforceSchema", False)
        .option("nullValue", "").option("timestampFormat", "yyyy-MM-dd'T'HH:mm:ssXXX")
        .csv(f"Files/energy-demo/tables/{name}.csv").cache()
    )
    count = frame.count()
    key = table["key"]
    if frame.filter(F.col(key).isNull()).count() or frame.select(key).distinct().count() != count:
        raise ValueError(f"{name}: null or duplicate primary key")
    for column in columns:
        if not column["nullable"] and frame.filter(F.col(column["name"]).isNull()).count():
            raise ValueError(f"{name}.{column['name']}: unexpected null")
    expected = manifest["rowCounts"][name]
    if count != expected:
        raise ValueError(f"{name}: expected {expected} rows, found {count}")
    frames[name] = frame
    print(f"VALIDATED dbo.{name}: {count} rows")

by_name = {table["name"]: table for table in CONTRACT["tables"]}
for relation in CONTRACT["relationships"]:
    bridge = frames[relation["table"]]
    for endpoint, column in [
        (relation["source"], relation["source_key"]), (relation["target"], relation["target_key"])
    ]:
        pk = by_name[endpoint]["key"]
        refs = bridge.select(F.col(column).alias("_key")).where(F.col("_key").isNotNull()).distinct()
        keys = frames[endpoint].select(F.col(pk).alias("_key"))
        if refs.join(keys, "_key", "left_anti").limit(1).count():
            raise ValueError(f"{relation['name']}: orphan {endpoint} reference")

if APPLY:
    for name, frame in frames.items():
        frame.write.format("delta").mode("errorifexists").saveAsTable(f"dbo.{name}")
        if spark.table(f"dbo.{name}").count() != frame.count():
            raise ValueError(f"{name}: persisted count mismatch")
    print("Created source Delta tables. Ontology bindings and graph refresh are separate steps.")
else:
    print("PREVIEW ONLY: no tables written. Review the target and counts before setting APPLY=True.")
for frame in frames.values():
    frame.unpersist()
"""
    return {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {
            "kernelspec": {"display_name": "Synapse PySpark", "language": "python", "name": "synapse_pyspark"}
        },
        "cells": [
            {
                "cell_type": "code",
                "id": "load-energy-tables",
                "execution_count": None,
                "metadata": {},
                "outputs": [],
                "source": code.splitlines(keepends=True),
            }
        ],
    }


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", type=Path, required=True)
    parser.add_argument("--workspace")
    parser.add_argument("--lakehouse")
    parser.add_argument(
        "--table-names", type=Path, help="JSON map from contract table names to discovered physical names"
    )
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    pack = cast(Path, args.pack)
    schema = object_value(json.loads((pack / "schema.json").read_text(encoding="utf-8")))
    table_names = None
    if args.table_names is not None:
        raw_names = object_value(json.loads(cast(Path, args.table_names).read_text(encoding="utf-8")))
        table_names = {identifier(name): identifier(value) for name, value in raw_names.items()}
    definition, enrichment = compile_ontology(
        schema, workspace=args.workspace, lakehouse=args.lakehouse, table_names=table_names
    )
    destination = pack / "fabric"
    if destination.exists() and any(destination.iterdir()) and not args.overwrite:
        raise FileExistsError("Fabric artifacts already exist; pass --overwrite to replace only generated files")
    destination.mkdir(parents=True, exist_ok=True)
    bound = bool(args.workspace and args.lakehouse)
    name = "ontology-definition.bound.json" if bound else "ontology-blueprint.UNBOUND.json"
    write_json(destination / name, definition)
    write_json(destination / "semantic-enrichment.json", enrichment)
    write_json(
        destination / "load-energy-tables.ipynb",
        notebook(schema, workspace=args.workspace, lakehouse=args.lakehouse),
    )
    write_json(
        destination / "binding-plan.json",
        {
            "state": "compiled-not-deployed" if bound else "unbound-not-deployable",
            "lakehouseName": "IndonesiaEnergyDemoLH",
            "ontologyName": "IndonesiaEnergyDemoOntology",
            "tables": [table.name for table in parse_tables(schema)],
            "physicalTableNames": table_names,
            "relationships": schema["relationships"],
            "enrichment": (
                "Descriptions, entity synonyms and relationship attributes are included, following the "
                "current REST definition article. The older JSON schemas do not fully validate enrichment. "
                "Back up first, use a subscription-scoped CLI context, and re-read after applying. "
                "Relationship metadata is not currently consumed by the public data-agent experience."
            ),
            "deployment": (
                "No cloud writes were performed. Create a dedicated lakehouse, load tables, compile with its "
                "actual UUID, create the ontology from its definition, open its generated Graph in Fabric "
                "to initialize loading infrastructure, refresh, verify graph counts and discover "
                "graphModelId before switching the application catalog."
            ),
            "dataAgent": "Separate optional Fabric item. An ontology MCP endpoint is not a Fabric Data Agent.",
        },
    )
    print(f"Wrote {destination}; bound={bound}; no Azure calls or writes performed.")


if __name__ == "__main__":
    main()
