from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import cast


@dataclass(frozen=True)
class ResourceInventory:
    resources: dict[str, dict[str, object]]
    role_assignments: set[tuple[str, str, str]]


@dataclass(frozen=True)
class ParityResult:
    missing_in_terraform: set[str]
    missing_in_bicep: set[str]
    sku_mismatches: list[str]
    network_mismatches: list[str]
    identity_mismatches: list[str]
    role_assignment_mismatches: list[str]
    diagnostic_setting_mismatches: list[str]


def load_arm_inventory(template: dict[str, object] | Path | list[dict[str, object] | Path]) -> ResourceInventory:
    documents = template if isinstance(template, list) else [template]
    resources: dict[str, dict[str, object]] = {}
    roles: set[tuple[str, str, str]] = set()
    for template_or_path in documents:
        document = load_json(template_or_path)
        for resource in arm_resources(document):
            resource_type = string_value(resource.get("type"), "ARM resource type")
            if resource_type == "Microsoft.Authorization/roleAssignments":
                properties = object_value(resource.get("properties"), "ARM role properties")
                roles.add(
                    (
                        normalized_scope(string_value(resource.get("scope", ""), "ARM role scope")),
                        string_value(properties.get("principalId"), "ARM role principal"),
                        normalized_role_id(string_value(properties.get("roleDefinitionId"), "ARM role definition")),
                    )
                )
                continue
            logical_identity = arm_resource_identity(resource)
            insert_inventory_resource(
                resources,
                resource_type,
                logical_identity,
                {
                    "logical_identity": logical_identity,
                    "resource_type": resource_type,
                    "sku": nested_string(resource, "sku", "name"),
                    "network": nested_string(resource, "properties", "publicNetworkAccess"),
                    "identity": nested_string(resource, "identity", "type"),
                    "diagnostics": resource_type.endswith("/diagnosticSettings"),
                },
                duplicate_label="ARM",
            )
    return ResourceInventory(resources=resources, role_assignments=roles)


def load_terraform_inventory(plan: dict[str, object] | Path) -> ResourceInventory:
    document = load_json(plan)
    planned_values = object_value(document.get("planned_values"), "Terraform planned values")
    root_module = object_value(planned_values.get("root_module"), "Terraform root module")
    resources: dict[str, dict[str, object]] = {}
    roles: set[tuple[str, str, str]] = set()
    for resource in terraform_resources(root_module):
        if resource.get("mode") != "managed":
            continue
        values = object_value(resource.get("values"), "Terraform resource values")
        terraform_type = string_value(resource.get("type"), "Terraform resource type")
        if terraform_type == "azurerm_role_assignment":
            roles.add(
                (
                    normalized_scope(string_value(values.get("scope"), "Terraform role scope")),
                    string_value(values.get("principal_id"), "Terraform role principal"),
                    normalized_role_id(string_value(values.get("role_definition_id"), "Terraform role definition")),
                )
            )
            continue
        resource_type = terraform_resource_type(terraform_type, values)
        logical_identity = terraform_resource_identity(terraform_type, values, resource)
        insert_inventory_resource(
            resources,
            resource_type,
            logical_identity,
            {
                "logical_identity": logical_identity,
                "resource_type": resource_type,
                "sku": first_string(values, "sku_name", "sku", "sku_tier"),
                "network": terraform_network_access(values),
                "identity": terraform_identity(values),
                "diagnostics": terraform_type.endswith("diagnostic_setting"),
            },
            duplicate_label="Terraform",
        )
    return ResourceInventory(resources=resources, role_assignments=roles)


def compare_inventories(bicep: ResourceInventory, terraform: ResourceInventory) -> ParityResult:
    bicep_keys = set(bicep.resources)
    terraform_keys = set(terraform.resources)
    common_keys = sorted(bicep_keys & terraform_keys)
    sku_mismatches = compare_property(bicep, terraform, common_keys, "sku")
    network_mismatches = compare_property(bicep, terraform, common_keys, "network")
    identity_mismatches = compare_property(bicep, terraform, common_keys, "identity")
    bicep_diagnostics = {name for name, details in bicep.resources.items() if details["diagnostics"]}
    terraform_diagnostics = {name for name, details in terraform.resources.items() if details["diagnostics"]}
    role_mismatches = sorted(bicep.role_assignments ^ terraform.role_assignments)
    return ParityResult(
        missing_in_terraform=bicep_keys - terraform_keys,
        missing_in_bicep=terraform_keys - bicep_keys,
        sku_mismatches=sku_mismatches,
        network_mismatches=network_mismatches,
        identity_mismatches=identity_mismatches,
        role_assignment_mismatches=["|".join(item) for item in role_mismatches],
        diagnostic_setting_mismatches=sorted(bicep_diagnostics ^ terraform_diagnostics),
    )


def compare_property(
    bicep: ResourceInventory,
    terraform: ResourceInventory,
    resource_types: list[str],
    property_name: str,
) -> list[str]:
    mismatches: list[str] = []
    for resource_type in resource_types:
        left = bicep.resources[resource_type][property_name]
        right = terraform.resources[resource_type][property_name]
        if left is not None and right is not None and left != right:
            mismatches.append(resource_type)
    return mismatches


def arm_resources(document: dict[str, object]) -> list[dict[str, object]]:
    resources = object_list(document.get("resources", []), "ARM resources")
    result: list[dict[str, object]] = []
    for resource in resources:
        resource_object = object_value(resource, "ARM resource")
        result.append(resource_object)
        properties = resource_object.get("properties")
        if isinstance(properties, dict):
            template = object_value(cast(object, properties), "ARM resource properties").get("template")
            if isinstance(template, dict):
                result.extend(arm_resources(object_value(cast(object, template), "ARM nested template")))
    return result


def terraform_resources(module: dict[str, object]) -> list[dict[str, object]]:
    result = [
        object_value(resource, "Terraform resource")
        for resource in object_list(module.get("resources", []), "resources")
    ]
    for child in object_list(module.get("child_modules", []), "child modules"):
        result.extend(terraform_resources(object_value(child, "Terraform child module")))
    return result


def terraform_resource_type(terraform_type: str, values: dict[str, object]) -> str:
    azapi_type = values.get("type")
    if isinstance(azapi_type, str) and "@" in azapi_type:
        return azapi_type.split("@", maxsplit=1)[0]
    explicit = values.get("resource_type")
    if isinstance(explicit, str):
        return explicit
    return f"terraform:{terraform_type}"


def inventory_key(resource_type: str, logical_identity: str) -> str:
    return f"{resource_type}|{logical_identity}"


def insert_inventory_resource(
    resources: dict[str, dict[str, object]],
    resource_type: str,
    logical_identity: str,
    payload: dict[str, object],
    duplicate_label: str,
) -> None:
    duplicate_key = inventory_key(resource_type, logical_identity)
    if duplicate_key in resources:
        raise ValueError(f"duplicate {duplicate_label} resource inventory key: {duplicate_key}")

    singleton_key = resource_type
    if singleton_key not in resources:
        resources[singleton_key] = payload
        return

    existing = resources.pop(singleton_key)
    existing_identity = string_value(existing.get("logical_identity"), f"{duplicate_label} logical identity")
    if existing_identity == logical_identity:
        raise ValueError(f"duplicate {duplicate_label} resource inventory key: {duplicate_key}")
    resources[inventory_key(resource_type, existing_identity)] = existing
    resources[duplicate_key] = payload


def arm_resource_identity(resource: dict[str, object]) -> str:
    scope = resource.get("scope")
    if isinstance(scope, str) and scope:
        return normalized_scope(scope)
    name = resource.get("name")
    if isinstance(name, str):
        return normalize_identity_text(name)
    return json.dumps(resource, sort_keys=True)


def terraform_resource_identity(terraform_type: str, values: dict[str, object], resource: dict[str, object]) -> str:
    for field in ("logical_name", "name", "scope"):
        candidate = values.get(field)
        if isinstance(candidate, str) and candidate:
            return normalize_identity_text(candidate)
    address = resource.get("address")
    if isinstance(address, str) and address:
        return address
    return terraform_type


def normalize_identity_text(value: str) -> str:
    return " ".join(value.split())


def terraform_network_access(values: dict[str, object]) -> str | None:
    enabled = values.get("public_network_access_enabled")
    if isinstance(enabled, bool):
        return "Enabled" if enabled else "Disabled"
    return first_string(values, "public_network_access")


def terraform_identity(values: dict[str, object]) -> str | None:
    identities = object_list(values.get("identity", []), "Terraform identity")
    if identities and isinstance(identities[0], dict):
        return first_string(object_value(cast(object, identities[0]), "Terraform identity entry"), "type")
    return None


def nested_string(value: dict[str, object], outer: str, inner: str) -> str | None:
    nested = value.get(outer)
    if not isinstance(nested, dict):
        return None
    return first_string(object_value(cast(object, nested), f"nested {outer}"), inner)


def first_string(value: dict[str, object], *names: str) -> str | None:
    for name in names:
        candidate = value.get(name)
        if isinstance(candidate, str):
            return candidate
    return None


def normalized_scope(value: str) -> str:
    if value.startswith("[resourceId('") and value.endswith(")]"):
        resource_id_arguments = value.removeprefix("[resourceId('").removesuffix(")]")
        return resource_id_arguments.split("', '", maxsplit=1)[0]
    return value


def normalized_role_id(value: str) -> str:
    return value.rsplit("'", 2)[-2] if "'" in value else value


def load_json(value: dict[str, object] | Path) -> dict[str, object]:
    if isinstance(value, Path):
        parsed = json.loads(value.read_text(encoding="utf-8"))
        return object_value(parsed, "JSON document")
    return value


def object_value(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return cast(dict[str, object], value)


def object_list(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array")
    return cast(list[object], value)


def string_value(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare normalized Bicep ARM and Terraform plan inventories.")
    parser.add_argument("--arm", type=Path, required=True, action="append")
    parser.add_argument("--terraform", type=Path, required=True)
    arguments = parser.parse_args()
    result = compare_inventories(load_arm_inventory(arguments.arm), load_terraform_inventory(arguments.terraform))
    mismatch_count = sum(
        len(value)
        for value in (
            result.missing_in_terraform,
            result.missing_in_bicep,
            result.sku_mismatches,
            result.network_mismatches,
            result.identity_mismatches,
            result.role_assignment_mismatches,
            result.diagnostic_setting_mismatches,
        )
    )
    if mismatch_count:
        print(json.dumps(result.__dict__, default=sorted, sort_keys=True))
        return 1
    print("PASS: 0 missing, 0 extra, 0 SKU/network/identity/role/diagnostic mismatches")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
