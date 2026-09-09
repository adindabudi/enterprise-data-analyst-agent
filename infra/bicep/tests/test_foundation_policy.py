import importlib.util
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = ROOT / "scripts/verify-iac-inventory.py"
SPEC = importlib.util.spec_from_file_location("verify_iac_inventory", SCRIPT_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"unable to load inventory extractor: {SCRIPT_PATH}")

MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
build_bicep_template = MODULE.build_bicep_template
iter_resources = MODULE.iter_resources


def compiled_resources() -> tuple[Mapping[str, Any], ...]:
    template = build_bicep_template(ROOT / "infra/bicep")
    return tuple(cast(Iterable[Mapping[str, Any]], iter_resources(template)))


def resources_of_type(resources: Iterable[Mapping[str, Any]], resource_type: str) -> tuple[Mapping[str, Any], ...]:
    return tuple(resource for resource in resources if resource.get("type") == resource_type)


def child_resource_name(resource: Mapping[str, Any]) -> str:
    name = str(resource["name"])
    if name.startswith("[format("):
        return name.rsplit("'", maxsplit=2)[1]
    return name.split("/")[-1]


def test_foundation_has_only_runtime_identities_and_conditional_fabric_provisioner() -> None:
    identities = resources_of_type(compiled_resources(), "Microsoft.ManagedIdentity/userAssignedIdentities")
    assert len(identities) == 4
    identity_names = {str(identity["name"]) for identity in identities}
    assert any("id-eda-web-" in name for name in identity_names)
    assert any("id-eda-worker-" in name for name in identity_names)
    assert any("id-eda-session-init-" in name for name in identity_names)
    [fabric_provisioner] = [identity for identity in identities if "provisioningIdentityName" in str(identity["name"])]
    assert "enabled" in str(fabric_provisioner.get("condition", ""))


def test_network_has_exactly_the_runtime_and_private_endpoint_subnets() -> None:
    resources = compiled_resources()
    subnets = resources_of_type(resources, "Microsoft.Network/virtualNetworks/subnets")
    assert len(subnets) == 4
    subnet_names = {child_resource_name(subnet) for subnet in subnets}
    assert subnet_names == {"aca", "foundry-agents-v2", "private-endpoints", "sandboxes"}
    foundry_subnet = next(subnet for subnet in subnets if child_resource_name(subnet) == "foundry-agents-v2")
    assert foundry_subnet["properties"]["addressPrefix"] == "10.42.7.0/24"
    assert "Microsoft.App/environments" in str(foundry_subnet["properties"]["delegations"])
    assert "sandboxes" in str(foundry_subnet["dependsOn"])
    sandbox_subnet = next(subnet for subnet in subnets if child_resource_name(subnet) == "sandboxes")
    assert "private-endpoints" in str(sandbox_subnet["dependsOn"])
    assert not resources_of_type(resources, "Microsoft.Network/publicIPAddresses")


def test_foundry_replacement_has_a_fresh_account_name() -> None:
    naming = (ROOT / "infra/bicep/modules/naming.bicep").read_text(encoding="utf-8")

    assert "foundry-eda-${environmentName}-${suffix}-v2" in naming


def test_private_data_policy_creates_dns_zones_independent_of_profile() -> None:
    zones = resources_of_type(compiled_resources(), "Microsoft.Network/privateDnsZones")
    zone_names = {str(zone["name"]) for zone in zones}
    assert {
        "privatelink.blob.core.windows.net",
        "privatelink.documents.azure.com",
        "privatelink.redis.azure.net",
    }.issubset(zone_names)
    assert all("privateDataEndpointsEnabled" in str(zone.get("condition", "")) for zone in zones)
    assert all("production" not in str(zone.get("condition", "")) for zone in zones)


def test_product_data_public_access_parameter_is_mapped_from_environment() -> None:
    parameters = (ROOT / "infra/bicep/main.parameters.json").read_text(encoding="utf-8")

    assert '"productDataPublicAccessEnabled"' in parameters
    assert "${PRODUCT_DATA_PUBLIC_ACCESS_ENABLED=false}" in parameters


def test_monitoring_and_registry_follow_foundation_policy() -> None:
    resources = compiled_resources()
    workspaces = resources_of_type(resources, "Microsoft.OperationalInsights/workspaces")
    insights = resources_of_type(resources, "Microsoft.Insights/components")
    registries = resources_of_type(resources, "Microsoft.ContainerRegistry/registries")
    assignments = resources_of_type(resources, "Microsoft.Authorization/roleAssignments")

    assert len(workspaces) == 1
    assert len(insights) == 1
    assert "Microsoft.OperationalInsights/workspaces" in str(insights[0]["properties"]["WorkspaceResourceId"])
    assert workspaces[0]["location"] == insights[0]["location"]
    assert len(registries) == 1
    assert registries[0]["properties"]["adminUserEnabled"] is False
    assert registries[0]["sku"]["name"] == "Standard"
    retention = registries[0]["properties"].get("policies", {}).get("retentionPolicy")
    assert retention is None or retention["status"] != "enabled"

    assignment_text = " ".join(str(assignment) for assignment in assignments).lower()
    assert "b24988ac-6180-42a0-ab88-20f7382dd24c" not in assignment_text
    assert "8e3af657-a8ff-443c-a75c-2fe8c4bcb635" not in assignment_text
