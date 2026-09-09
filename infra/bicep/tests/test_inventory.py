import importlib.util
import sys
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
load_bicep_inventory = cast(Any, MODULE.load_bicep_inventory)
build_bicep_template = cast(Any, MODULE.build_bicep_template)
iter_resources = cast(Any, MODULE.iter_resources)


def test_reference_topology_has_one_of_each_stateful_service() -> None:
    inventory = load_bicep_inventory(ROOT / "infra/bicep")
    assert inventory.count("Microsoft.CognitiveServices/accounts") == 1
    assert inventory.count("Microsoft.CognitiveServices/accounts/projects") == 1
    assert inventory.count("Microsoft.CognitiveServices/accounts/deployments") == 1
    assert inventory.count("Microsoft.DurableTask/schedulers") == 0
    assert inventory.count("Microsoft.DurableTask/schedulers/taskHubs") == 0
    assert inventory.count("Microsoft.Cache/redisEnterprise") == 1
    assert inventory.count("Microsoft.Cache/redisEnterprise/databases") == 1
    assert inventory.count("Microsoft.DocumentDB/databaseAccounts") == 1
    assert inventory.count("Microsoft.Storage/storageAccounts") == 1
    assert inventory.count("Microsoft.App/managedEnvironments") == 1
    assert inventory.count("Microsoft.App/sessionPools") == 0
    assert inventory.count("Microsoft.App/sandboxGroups") == 1


def test_no_unapproved_service_enters_reference_topology() -> None:
    inventory = load_bicep_inventory(ROOT / "infra/bicep")
    prohibited = {
        "Microsoft.ApiManagement/service",
        "Microsoft.ContainerService/managedClusters",
    }
    assert prohibited.isdisjoint(inventory.resource_types)

    resources = tuple(iter_resources(build_bicep_template(ROOT / "infra/bicep")))
    [fabric_vault] = [resource for resource in resources if resource.get("type") == "Microsoft.KeyVault/vaults"]
    assert "enabled" in str(fabric_vault.get("condition", ""))


def test_all_nested_deployments_use_inner_expression_evaluation() -> None:
    template = build_bicep_template(ROOT / "infra/bicep")
    deployments = [
        resource
        for resource in iter_resources(template)
        if resource.get("type", "").lower() == "microsoft.resources/deployments"
    ]

    assert deployments
    assert all(
        deployment["properties"].get("expressionEvaluationOptions", {}).get("scope") == "inner"
        for deployment in deployments
    )
