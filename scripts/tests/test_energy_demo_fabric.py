from __future__ import annotations

import base64
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "energy_demo_fabric.py"


def load() -> Any:
    spec = importlib.util.spec_from_file_location("energy_demo_fabric", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def fixture() -> dict[str, object]:
    return {
        "tables": [
            {
                "name": name,
                "key": key,
                "description": f"One row per {name}.",
                "synonyms": [name.lower()],
                "ontology": True,
                "columns": [
                    {"name": column, "type": "String", "description": column, "nullable": False} for column in columns
                ],
            }
            for name, key, columns in (
                ("Field", "FieldId", ["FieldId"]),
                ("Well", "WellId", ["WellId", "FieldId"]),
            )
        ],
        "relationships": [
            {
                "name": "well_in_field",
                "source": "Well",
                "target": "Field",
                "table": "Well",
                "source_key": "WellId",
                "target_key": "FieldId",
                "description": "Well belongs to one field.",
                "cardinality": "many-to-one",
            }
        ],
    }


def bodies(definition: dict[str, Any]) -> dict[str, Any]:
    return {
        entry["path"]: json.loads(base64.b64decode(entry["payload"])) for entry in definition["definition"]["parts"]
    }


def test_unbound_blueprint_has_no_target_identifiers_or_data_bindings() -> None:
    module = load()
    definition, enrichment = module.compile_ontology(fixture())
    assert len(bodies(definition)) == 4
    assert "workspaceId" not in json.dumps(bodies(definition))
    assert enrichment["entityTypes"]["Well"]["synonyms"] == ["well"]
    assert "synonyms" not in enrichment["relationshipTypes"]["well_in_field"]


def test_bound_definition_joins_parent_primary_key_not_child_foreign_key_id() -> None:
    module = load()
    definition, _ = module.compile_ontology(
        fixture(),
        workspace="11111111-1111-4111-8111-111111111111",
        lakehouse="22222222-2222-4222-8222-222222222222",
    )
    decoded = bodies(definition)
    contextualization = next(value for path, value in decoded.items() if "/Contextualizations/" in path)
    assert contextualization["targetKeyRefBindings"] == [
        {"sourceColumnName": "FieldId", "targetPropertyId": module.property_id("Field", "FieldId")}
    ]
    assert contextualization["dataBindingTable"]["sourceTableName"] == "Well"
    assert next(iter(contextualization["dataBindingTable"])) == "sourceType"
    for path, value in decoded.items():
        if "/DataBindings/" in path:
            configuration = value["dataBindingConfiguration"]
            assert next(iter(configuration)) == "dataBindingType"
            assert next(iter(configuration["sourceTableProperties"])) == "sourceType"
    assert len(decoded) == 7


def test_ids_do_not_change_when_table_order_changes() -> None:
    module = load()
    schema = fixture()
    left, _ = module.compile_ontology(schema)
    schema["tables"] = list(reversed(schema["tables"]))
    right, _ = module.compile_ontology(schema)
    assert bodies(left) == bodies(right)


def test_physical_name_mapping_changes_bindings_not_entity_labels_or_key_ids() -> None:
    module = load()
    definition, _ = module.compile_ontology(
        fixture(),
        workspace="11111111-1111-4111-8111-111111111111",
        lakehouse="22222222-2222-4222-8222-222222222222",
        table_names={"Field": "field", "Well": "well"},
    )
    decoded = bodies(definition)
    well = decoded[f"EntityTypes/{module.stable_id('entity', 'Well')}/definition.json"]
    assert well["name"] == "Well"
    assert well["entityIdParts"] == [module.property_id("Well", "WellId")]
    binding = next(value for path, value in decoded.items() if "/Contextualizations/" in path)
    assert binding["dataBindingTable"]["sourceTableName"] == "well"
    entity_binding = next(
        value
        for path, value in decoded.items()
        if path.startswith(f"EntityTypes/{module.stable_id('entity', 'Well')}/DataBindings/")
    )
    assert entity_binding["dataBindingConfiguration"]["sourceTableProperties"]["sourceTableName"] == "well"


def test_physical_name_mapping_rejects_incomplete_or_ambiguous_targets() -> None:
    module = load()
    with pytest.raises(ValueError, match="exactly"):
        module.compile_ontology(fixture(), table_names={"Well": "well"})
    with pytest.raises(ValueError, match="duplicate"):
        module.compile_ontology(fixture(), table_names={"Well": "field", "Field": "field"})


def test_partial_target_and_nil_target_are_rejected() -> None:
    module = load()
    with pytest.raises(ValueError, match="both"):
        module.compile_ontology(fixture(), workspace="11111111-1111-4111-8111-111111111111")
    with pytest.raises(ValueError, match="nil"):
        module.compile_ontology(
            fixture(),
            workspace="00000000-0000-0000-0000-000000000000",
            lakehouse="22222222-2222-4222-8222-222222222222",
        )


def test_missing_relationship_key_is_rejected() -> None:
    module = load()
    schema = fixture()
    schema["relationships"][0]["target_key"] = "Missing"
    with pytest.raises(ValueError, match="binding column"):
        module.compile_ontology(schema)


def test_notebook_is_create_only_and_never_reads_answer_oracles() -> None:
    module = load()
    value = module.notebook(fixture())
    source = "".join(value["cells"][0]["source"])
    compile(source, "load-energy-tables.ipynb", "exec")
    assert "APPLY = False" in source
    assert '.mode("errorifexists")' in source
    assert "evaluation" not in source
    assert "left_anti" in source
    assert "inferSchema" not in source
