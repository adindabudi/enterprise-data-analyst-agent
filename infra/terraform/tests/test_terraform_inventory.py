import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = ROOT / "scripts/verify_iac_parity.py"
SPEC = importlib.util.spec_from_file_location("verify_iac_parity", SCRIPT_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"unable to load parity comparator: {SCRIPT_PATH}")

MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
compare_inventories = MODULE.compare_inventories
load_arm_inventory = MODULE.load_arm_inventory
load_terraform_inventory = MODULE.load_terraform_inventory

FIXTURE_ARM_TEMPLATE = {
    "resources": [
        {
            "type": "Microsoft.Storage/storageAccounts",
            "apiVersion": "2023-05-01",
            "name": "reference-storage",
            "sku": {"name": "Standard_LRS"},
            "identity": {"type": "SystemAssigned"},
            "properties": {"publicNetworkAccess": "Disabled"},
        },
        {
            "type": "Microsoft.Authorization/roleAssignments",
            "apiVersion": "2022-04-01",
            "name": "storage-reader",
            "scope": "[resourceId('Microsoft.Storage/storageAccounts', 'reference-storage')]",
            "properties": {
                "principalId": "reader-principal",
                "roleDefinitionId": (
                    "[subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'reader-role')]"
                ),
            },
        },
    ]
}

FIXTURE_PLAN_JSON = {
    "planned_values": {
        "root_module": {
            "resources": [
                {
                    "address": "module.storage.azurerm_storage_account.this",
                    "mode": "managed",
                    "type": "azurerm_storage_account",
                    "name": "this",
                    "values": {
                        "name": "different-storage-name",
                        "resource_type": "Microsoft.Storage/storageAccounts",
                        "api_version": "2023-05-01",
                        "sku_name": "Standard_LRS",
                        "public_network_access_enabled": False,
                        "identity": [{"type": "SystemAssigned"}],
                        "tags": {"source": "terraform"},
                    },
                },
                {
                    "address": "module.storage.azurerm_role_assignment.reader",
                    "mode": "managed",
                    "type": "azurerm_role_assignment",
                    "name": "reader",
                    "values": {
                        "scope": "Microsoft.Storage/storageAccounts",
                        "principal_id": "reader-principal",
                        "role_definition_id": "reader-role",
                    },
                },
            ]
        }
    }
}


def test_parity_comparator_flags_missing_and_extra_resources() -> None:
    bicep_inventory = load_arm_inventory(FIXTURE_ARM_TEMPLATE)
    terraform_inventory = load_terraform_inventory(FIXTURE_PLAN_JSON)

    result = compare_inventories(bicep_inventory, terraform_inventory)

    assert result.missing_in_terraform == set()
    assert result.missing_in_bicep == set()
    assert result.sku_mismatches == []
    assert result.network_mismatches == []
    assert result.identity_mismatches == []
    assert result.role_assignment_mismatches == []
    assert result.diagnostic_setting_mismatches == []


def test_parity_comparator_is_sensitive_to_dropped_role_assignment() -> None:
    plan_without_role_assignment = {
        "planned_values": {
            "root_module": {"resources": FIXTURE_PLAN_JSON["planned_values"]["root_module"]["resources"][:1]}
        }
    }

    result = compare_inventories(
        load_arm_inventory(FIXTURE_ARM_TEMPLATE), load_terraform_inventory(plan_without_role_assignment)
    )

    assert result.role_assignment_mismatches != []


def test_parity_comparator_keeps_multiple_resources_of_the_same_type_distinct() -> None:
    arm_templates = [
        {
            "resources": [
                {
                    "type": "Microsoft.Storage/storageAccounts",
                    "apiVersion": "2023-05-01",
                    "name": "primary-storage",
                    "sku": {"name": "Standard_LRS"},
                    "properties": {"publicNetworkAccess": "Enabled"},
                }
            ]
        },
        {
            "resources": [
                {
                    "type": "Microsoft.Storage/storageAccounts",
                    "apiVersion": "2023-05-01",
                    "name": "secondary-storage",
                    "sku": {"name": "Standard_LRS"},
                    "properties": {"publicNetworkAccess": "Disabled"},
                }
            ]
        },
    ]
    terraform_plan = {
        "planned_values": {
            "root_module": {
                "resources": [
                    {
                        "address": "module.storage.azurerm_storage_account.primary",
                        "mode": "managed",
                        "type": "azurerm_storage_account",
                        "name": "primary",
                        "values": {
                            "name": "primary-storage",
                            "resource_type": "Microsoft.Storage/storageAccounts",
                            "sku_name": "Standard_LRS",
                            "public_network_access_enabled": True,
                        },
                    },
                    {
                        "address": "module.storage.azurerm_storage_account.secondary",
                        "mode": "managed",
                        "type": "azurerm_storage_account",
                        "name": "secondary",
                        "values": {
                            "name": "secondary-storage",
                            "resource_type": "Microsoft.Storage/storageAccounts",
                            "sku_name": "Standard_LRS",
                            "public_network_access_enabled": False,
                        },
                    },
                ]
            }
        }
    }

    result = compare_inventories(load_arm_inventory(arm_templates), load_terraform_inventory(terraform_plan))

    assert result.missing_in_terraform == set()
    assert result.missing_in_bicep == set()
    assert result.network_mismatches == []


def test_arm_inventory_rejects_duplicate_logical_resources_across_inputs() -> None:
    duplicate_template = {
        "resources": [
            {
                "type": "Microsoft.Storage/storageAccounts",
                "apiVersion": "2023-05-01",
                "name": "duplicate-storage",
                "sku": {"name": "Standard_LRS"},
            }
        ]
    }

    try:
        load_arm_inventory([duplicate_template, duplicate_template])
    except ValueError as error:
        assert "duplicate ARM resource inventory key" in str(error)
    else:
        raise AssertionError("expected duplicate logical resources to be rejected")
