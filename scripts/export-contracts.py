from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from eda_contracts import ActivityEvent, ArtifactRecord, SessionSummary, SteeringRequest, TaskSummary
from eda_provenance import TaskManifest
from pydantic import TypeAdapter

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "packages/contracts/schema"

CONTRACTS: dict[str, Any] = {
    "activity-event": TypeAdapter(ActivityEvent),
    "artifact-record": ArtifactRecord,
    "session-summary": SessionSummary,
    "steering-request": SteeringRequest,
    "task-manifest": TaskManifest,
    "task-summary": TaskSummary,
}


def normalize_root_discriminator(schema: dict[str, Any]) -> None:
    discriminator = schema.get("discriminator")
    if not isinstance(discriminator, dict):
        return
    discriminator = cast(dict[str, Any], discriminator)

    if "type" not in schema:
        schema["type"] = "object"
    if "mapping" in discriminator:
        schema["discriminator"] = {key: value for key, value in discriminator.items() if key != "mapping"}

    property_name = discriminator.get("propertyName")
    definitions = schema.get("$defs")
    variants = schema.get("oneOf")
    if not isinstance(property_name, str) or not isinstance(definitions, dict) or not isinstance(variants, list):
        return
    definitions = cast(dict[str, Any], definitions)
    variants = cast(list[Any], variants)

    for variant_value in variants:
        if not isinstance(variant_value, dict):
            continue
        variant = cast(dict[str, Any], variant_value)
        reference = variant.get("$ref")
        if not isinstance(reference, str) or not reference.startswith("#/$defs/"):
            continue
        definition = definitions.get(reference.removeprefix("#/$defs/"))
        if not isinstance(definition, dict):
            continue
        definition = cast(dict[str, Any], definition)
        properties = definition.get("properties")
        if not isinstance(properties, dict) or property_name not in properties:
            continue
        required = definition.get("required")
        if required is None:
            definition["required"] = [property_name]
        elif isinstance(required, list):
            required = cast(list[Any], required)
            if property_name not in required:
                required.append(property_name)


def schema_for(contract: Any, schema_id: str) -> dict[str, Any]:
    if isinstance(contract, TypeAdapter):
        schema = contract.json_schema(mode="serialization")
    else:
        schema = contract.model_json_schema(mode="serialization")
    normalize_root_discriminator(schema)
    default_title = "".join(part.capitalize() for part in schema_id.split("-"))
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"https://schemas.enterprise-data-analyst.dev/{schema_id}/1.0",
        "title": default_title,
        **schema,
    }


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    expected: set[Path] = set()
    for name, contract in sorted(CONTRACTS.items()):
        destination = OUTPUT / f"{name}.schema.json"
        expected.add(destination)
        destination.write_text(
            json.dumps(schema_for(contract, name), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    for stale in OUTPUT.glob("*.schema.json"):
        if stale not in expected:
            stale.unlink()


if __name__ == "__main__":
    main()
