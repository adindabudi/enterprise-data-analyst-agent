import importlib.util
import json
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

RBAC_SCRIPT_PATH = ROOT / "scripts/verify-rbac.py"
RBAC_SPEC = importlib.util.spec_from_file_location("verify_rbac", RBAC_SCRIPT_PATH)
if RBAC_SPEC is None or RBAC_SPEC.loader is None:
    raise RuntimeError(f"unable to load RBAC verifier: {RBAC_SCRIPT_PATH}")

RBAC_MODULE = importlib.util.module_from_spec(RBAC_SPEC)
sys.modules[RBAC_SPEC.name] = RBAC_MODULE
RBAC_SPEC.loader.exec_module(RBAC_MODULE)
load_deployment_outputs = RBAC_MODULE.load_deployment_outputs
load_expected_assignments = RBAC_MODULE.load_expected_assignments
verify_rbac = RBAC_MODULE.verify

COSMOS_DATA_CONTRIBUTOR_ROLE_ID = "00000000-0000-0000-0000-000000000002"
BLOB_DATA_CONTRIBUTOR_ROLE_ID = "ba92f5b4-2d11-453d-a403-e96b0029c9fe"


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


def test_cosmos_has_canonical_database_containers_and_data_plane_rbac() -> None:
    resources = compiled_resources()
    accounts = resources_of_type(resources, "Microsoft.DocumentDB/databaseAccounts")
    databases = resources_of_type(resources, "Microsoft.DocumentDB/databaseAccounts/sqlDatabases")
    containers = resources_of_type(resources, "Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers")
    sql_assignments = resources_of_type(resources, "Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments")

    assert len(accounts) == 1
    assert accounts[0]["properties"]["disableLocalAuth"] is True
    assert accounts[0]["properties"]["enableAutomaticFailover"] is True
    assert accounts[0]["properties"]["consistencyPolicy"]["defaultConsistencyLevel"] == "Session"
    backup_policy = str(accounts[0]["properties"]["backupPolicy"])
    assert "periodicModeProperties" in backup_policy
    assert "backupIntervalInMinutes" in backup_policy
    assert "backupRetentionIntervalInHours" in backup_policy
    assert len(databases) == 1
    assert len(containers) == 4

    containers_by_name = {child_resource_name(container): container for container in containers}
    assert set(containers_by_name) == {"workspace", "auth", "runtime", "fabricAuth"}
    workspace = containers_by_name["workspace"]["properties"]["resource"]
    assert workspace["partitionKey"] == {
        "paths": ["/tenantId", "/ownerObjectId", "/sessionId"],
        "kind": "MultiHash",
        "version": 2,
    }
    auth = containers_by_name["auth"]["properties"]["resource"]
    runtime = containers_by_name["runtime"]["properties"]["resource"]
    assert auth["partitionKey"]["paths"] == ["/id"]
    assert auth["defaultTtl"] == -1
    assert runtime["partitionKey"]["paths"] == ["/id"]
    assert "defaultTtl" not in runtime
    fabric_auth = containers_by_name["fabricAuth"]
    assert "fabricEnabled" in str(fabric_auth.get("condition", ""))
    assert fabric_auth["properties"]["resource"]["partitionKey"] == {
        "paths": ["/tenantId", "/ownerObjectId"],
        "kind": "MultiHash",
        "version": 2,
    }
    assert fabric_auth["properties"]["resource"]["defaultTtl"] == -1

    assert len(sql_assignments) == 3
    assert any("acceptancePrincipalId" in str(assignment) for assignment in sql_assignments)
    assert all(
        "sqlRoleDefinitions" in str(assignment["properties"]["roleDefinitionId"]) for assignment in sql_assignments
    )
    assert all(
        "Microsoft.DocumentDB/databaseAccounts" in str(assignment["properties"]["scope"])
        for assignment in sql_assignments
    )
    assert COSMOS_DATA_CONTRIBUTOR_ROLE_ID in str(resources)


def test_storage_defender_and_blob_rbac_follow_data_plane_policy() -> None:
    resources = compiled_resources()
    storage_accounts = resources_of_type(resources, "Microsoft.Storage/storageAccounts")
    blob_services = resources_of_type(resources, "Microsoft.Storage/storageAccounts/blobServices")
    containers = resources_of_type(resources, "Microsoft.Storage/storageAccounts/blobServices/containers")
    defender_settings = resources_of_type(resources, "Microsoft.Security/defenderForStorageSettings")
    arm_assignments = resources_of_type(resources, "Microsoft.Authorization/roleAssignments")

    assert len(storage_accounts) == 1
    properties = storage_accounts[0]["properties"]
    assert properties["allowBlobPublicAccess"] is False
    assert properties["allowSharedKeyAccess"] is False
    assert properties["defaultToOAuthAuthentication"] is True
    assert properties["minimumTlsVersion"] == "TLS1_2"

    assert len(blob_services) == 1
    blob_properties = blob_services[0]["properties"]
    assert blob_properties["isVersioningEnabled"] is True
    assert blob_properties["deleteRetentionPolicy"] == {"enabled": True, "days": 30}
    assert blob_properties["containerDeleteRetentionPolicy"] == {"enabled": True, "days": 30}
    assert {child_resource_name(container) for container in containers} == {"quarantine", "sessions"}
    assert all(container["properties"]["publicAccess"] == "None" for container in containers)

    assert len(defender_settings) == 1
    defender = defender_settings[0]["properties"]
    assert defender["malwareScanning"]["blobScanResultsOptions"] == "BlobIndexTags"
    assert defender["malwareScanning"]["automatedResponse"] == "BlobSoftDelete"
    assert defender["malwareScanning"]["onUpload"]["isEnabled"] is True

    blob_assignments = [
        assignment
        for assignment in arm_assignments
        if "blobDataContributorRoleDefinitionId" in str(assignment["properties"]["roleDefinitionId"])
    ]
    assert len(blob_assignments) == 2
    assert BLOB_DATA_CONTRIBUTOR_ROLE_ID in str(resources)


def test_private_data_policy_controls_public_access_and_private_endpoints() -> None:
    resources = compiled_resources()
    data_resources = (
        *resources_of_type(resources, "Microsoft.DocumentDB/databaseAccounts"),
        *resources_of_type(resources, "Microsoft.Storage/storageAccounts"),
        *resources_of_type(resources, "Microsoft.Cache/redisEnterprise"),
    )
    private_endpoints = resources_of_type(resources, "Microsoft.Network/privateEndpoints")

    assert len(data_resources) == 3
    assert all(
        "productDataPublicAccessEnabled" in str(resource["properties"]["publicNetworkAccess"])
        for resource in data_resources
    )
    assert len(private_endpoints) >= 3
    policy_endpoints = [
        endpoint
        for endpoint in private_endpoints
        if any(group in str(endpoint) for group in ("cosmos-sql", "storage-blob", "redisEnterprise"))
    ]
    assert len(policy_endpoints) == 3
    assert all("productDataPublicAccessEnabled" in str(endpoint.get("condition", "")) for endpoint in policy_endpoints)
    assert all("production" not in str(endpoint.get("condition", "")) for endpoint in policy_endpoints)


def test_rbac_contract_loads_without_an_azure_login(tmp_path: Path) -> None:
    output_path = tmp_path / "outputs.json"
    output_path.write_text(
        json.dumps(
            {
                "webIdentityPrincipalId": {"value": "web"},
                "workerIdentityPrincipalId": {"value": "worker"},
                "storageAccountId": {"value": "/resourceGroups/rg/storage"},
                "sandboxGroupId": {"value": "/resourceGroups/rg/sandbox"},
                "cosmosAccountId": {"value": "/resourceGroups/rg/cosmos"},
                "redisClusterId": {"value": "/resourceGroups/rg/redis"},
            }
        ),
        encoding="utf-8",
    )

    outputs = load_deployment_outputs(output_path)
    expected = load_expected_assignments(ROOT / "scripts/rbac-assignments.json", outputs)

    assert len(expected.arm) == 3
    assert len(expected.cosmos_sql) == 2
    assert len(expected.redis_access_policies) == 1


def test_rbac_contract_verifies_redis_access_policy_assignments() -> None:
    outputs = {
        "webIdentityPrincipalId": "web",
        "workerIdentityPrincipalId": "worker",
        "storageAccountId": "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.Storage/storageAccounts/store",
        "sandboxGroupId": "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.App/sandboxGroups/sbg",
        "cosmosAccountId": "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.DocumentDB/databaseAccounts/cosmos",
        "redisClusterId": "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.Cache/redisEnterprise/cache",
    }
    expected = load_expected_assignments(ROOT / "scripts/rbac-assignments.json", outputs)
    calls: list[tuple[str, ...]] = []

    def runner(arguments: list[str]) -> list[dict[str, Any]]:
        calls.append(tuple(arguments))
        if arguments[:2] == ["role", "assignment"]:
            if outputs["sandboxGroupId"].lower() in arguments:
                return [
                    {
                        "principalId": "web",
                        "scope": outputs["sandboxGroupId"],
                        "roleDefinitionId": "c24cf47c-5077-412d-a19c-45202126392c",
                    }
                ]
            return [
                {
                    "principalId": principal,
                    "scope": outputs["storageAccountId"],
                    "roleDefinitionId": BLOB_DATA_CONTRIBUTOR_ROLE_ID,
                }
                for principal in ("web", "worker")
            ]
        if arguments[:4] == ["cosmosdb", "sql", "role", "assignment"]:
            return [
                {
                    "principalId": principal,
                    "scope": "/",
                    "roleDefinitionId": COSMOS_DATA_CONTRIBUTOR_ROLE_ID,
                }
                for principal in ("web", "worker")
            ]
        if arguments[:3] == ["rest", "--method", "GET"]:
            return [
                {
                    "name": name,
                    "properties": {"accessPolicyName": "default", "user": {"objectId": principal}},
                }
                for name, principal in (("web", "web"), ("worker", "worker"))
            ]
        return []

    verify_rbac(outputs, expected, runner)

    assert any(
        command[:3] == ("rest", "--method", "GET")
        and command[-2:] == ("--query", "value")
        and f"{outputs['redisClusterId']}/databases/default/accessPolicyAssignments" in command[4]
        for command in calls
    )
