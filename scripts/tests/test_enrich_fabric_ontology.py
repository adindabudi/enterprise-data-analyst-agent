from __future__ import annotations

import base64
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "enrich-fabric-ontology.py"
ENTITY_PATH = "EntityTypes/1/definition.json"


def load_module() -> Any:
    specification = importlib.util.spec_from_file_location("enrich_fabric_ontology", SCRIPT)
    assert specification is not None
    assert specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


def decoded(part: dict[str, str]) -> dict[str, Any]:
    return json.loads(base64.b64decode(part["payload"]))


def ontology_parts(
    *,
    entity_enrichment: dict[str, Any] | None = None,
    property_enrichment: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    equipment: dict[str, Any] = {
        "id": "1",
        "name": "vitalsignequipment",
        "properties": [{"id": "11", "name": "EquipmentType", "valueType": "String"}],
        "timeseriesProperties": [{"id": "12", "name": "OxygenSaturation", "valueType": "BigInt"}],
    }
    if entity_enrichment is not None:
        equipment["semanticEnrichment"] = entity_enrichment
    if property_enrichment is not None:
        equipment["properties"][0]["semanticEnrichment"] = property_enrichment
    return [
        {"path": "definition.json", "payload": "e30=", "payloadType": "InlineBase64"},
        {
            "path": ENTITY_PATH,
            "payload": base64.b64encode(json.dumps(equipment).encode()).decode(),
            "payloadType": "InlineBase64",
        },
    ]


def test_the_file_enriches_the_entity_and_the_property_it_names() -> None:
    module = load_module()

    updated, applied = module.enrich(
        ontology_parts(),
        {
            "entityTypes": {
                "vitalsignequipment": {
                    "description": "Bedside monitors.",
                    "synonyms": ["monitor"],
                    "properties": {"EquipmentType": {"description": "One of Spot-Check, Telemetry."}},
                }
            }
        },
    )

    body = decoded(updated[1])
    assert body["semanticEnrichment"] == {"description": "Bedside monitors.", "synonyms": ["monitor"]}
    assert body["properties"][0]["semanticEnrichment"] == {"description": "One of Spot-Check, Telemetry."}
    # updateDefinition replaces the whole item, so parts the file says nothing about must survive untouched.
    assert updated[0] == {"path": "definition.json", "payload": "e30=", "payloadType": "InlineBase64"}
    assert "vitalsignequipment: entity" in applied


def test_dropping_an_entry_from_the_file_clears_what_the_ontology_stored() -> None:
    module = load_module()

    updated, applied = module.enrich(
        ontology_parts(
            entity_enrichment={"description": "stale", "customAttributes": {}},
            property_enrichment={"description": "stale property"},
        ),
        {"entityTypes": {}},
    )

    body = decoded(updated[1])
    # Fabric keeps part-level enrichment when the field is merely absent, so a clear is sent as {}.
    assert body["semanticEnrichment"] == {}
    assert "semanticEnrichment" not in body["properties"][0]
    assert applied == ["vitalsignequipment: cleared", "vitalsignequipment.properties: EquipmentType (cleared)"]


def test_an_already_empty_enrichment_is_left_alone() -> None:
    module = load_module()

    updated, applied = module.enrich(
        ontology_parts(entity_enrichment={"description": None, "customAttributes": {}}),
        {"entityTypes": {}},
    )

    assert applied == []
    assert updated[1] == ontology_parts(entity_enrichment={"description": None, "customAttributes": {}})[1]


def test_an_entity_type_the_ontology_does_not_have_is_refused() -> None:
    module = load_module()

    with pytest.raises(ValueError, match="not in the ontology"):
        module.enrich(ontology_parts(), {"entityTypes": {"wards": {"description": "a unit"}}})


def test_synonyms_are_refused_on_anything_but_an_entity_type() -> None:
    module = load_module()

    with pytest.raises(ValueError, match="unsupported enrichment keys"):
        module.enrich(
            ontology_parts(),
            {"entityTypes": {"vitalsignequipment": {"properties": {"EquipmentType": {"synonyms": ["kind"]}}}}},
        )


def test_an_accepted_write_is_polled_until_the_operation_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_module()
    calls: list[tuple[str, str]] = []
    statuses = iter(["Running", "Succeeded"])

    def fake_call(method: str, url: str, bearer: str, body: object | None = None) -> httpx.Response:
        del bearer, body
        calls.append((method, url))
        if url.endswith("/updateDefinition"):
            return httpx.Response(202, headers={"x-ms-operation-id": "op-1", "Retry-After": "0"})
        return httpx.Response(200, json={"status": next(statuses)})

    monkeypatch.setattr(module, "call", fake_call)

    assert module.write_parts("https://fabric/item", "token", []) is True
    assert calls == [
        ("POST", "https://fabric/item/updateDefinition"),
        ("GET", f"{module.FABRIC}/v1/operations/op-1"),
        ("GET", f"{module.FABRIC}/v1/operations/op-1"),
    ]


def test_an_accepted_write_without_an_operation_id_is_not_reported_as_written(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_module()

    def fake_call(method: str, url: str, bearer: str, body: object | None = None) -> httpx.Response:
        del method, url, bearer, body
        return httpx.Response(202)

    monkeypatch.setattr(module, "call", fake_call)

    assert module.write_parts("https://fabric/item", "token", []) is False
