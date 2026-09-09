from __future__ import annotations

import importlib.util
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType
from uuid import UUID

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "bind-hosted-agent-rbac.py"
SUBSCRIPTION_ID = "11111111-1111-1111-1111-111111111111"
PRINCIPAL_ID = "22222222-2222-2222-2222-222222222222"
RESOURCE_GROUP = "rg-eda-demo"
PROJECT_ENDPOINT = "https://foundry.services.ai.azure.com/api/projects/eda-project"
AGENT_NAME = "enterprise-data-analyst-long-job"
STORAGE_ID = (
    f"/subscriptions/{SUBSCRIPTION_ID}/resourceGroups/{RESOURCE_GROUP}"
    "/providers/Microsoft.Storage/storageAccounts/steda"
)
COSMOS_ID = (
    f"/subscriptions/{SUBSCRIPTION_ID}/resourceGroups/{RESOURCE_GROUP}"
    "/providers/Microsoft.DocumentDB/databaseAccounts/cosmos-eda"
)
REDIS_ID = (
    f"/subscriptions/{SUBSCRIPTION_ID}/resourceGroups/{RESOURCE_GROUP}"
    "/providers/Microsoft.Cache/redisEnterprise/redis-eda"
)
SANDBOX_ID = (
    f"/subscriptions/{SUBSCRIPTION_ID}/resourceGroups/{RESOURCE_GROUP}"
    "/providers/Microsoft.App/sandboxGroups/sbg-eda-demo"
)
BLOB_ROLE_ID = "ba92f5b4-2d11-453d-a403-e96b0029c9fe"
COSMOS_ROLE_ID = "00000000-0000-0000-0000-000000000002"
SANDBOX_ROLE_ID = "c24cf47c-5077-412d-a19c-45202126392c"
FOUNDRY_USER_ROLE_ID = "53ca6127-db72-4b80-b1b0-d745d6d5456d"


def load_script() -> ModuleType:
    assert SCRIPT.is_file(), "Hosted Agent RBAC binder is missing"
    specification = importlib.util.spec_from_file_location("bind_hosted_agent_rbac", SCRIPT)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def azd_values(*, fabric_enabled: bool = False, include_agent_identity: bool = True) -> str:
    values = {
        "AZURE_SUBSCRIPTION_ID": SUBSCRIPTION_ID,
        "AZURE_RESOURCE_GROUP": RESOURCE_GROUP,
        "PROJECT_ENDPOINT": PROJECT_ENDPOINT,
        "AGENT_LONG_JOB_NAME": AGENT_NAME,
        "AZURE_AI_ACCOUNT_NAME": "foundry-eda",
        "STORAGE_ACCOUNT_ID": STORAGE_ID,
        "COSMOS_ACCOUNT_ID": COSMOS_ID,
        "REDIS_CLUSTER_ID": REDIS_ID,
        "EDA_SANDBOX_RESOURCE_GROUP": RESOURCE_GROUP,
        "EDA_SANDBOX_GROUP": "sbg-eda-demo",
        "FABRIC_ENABLED": "true" if fabric_enabled else "false",
    }
    if include_agent_identity:
        values["AGENT_LONG_JOB_INSTANCE_IDENTITY_PRINCIPAL_ID"] = PRINCIPAL_ID
    if fabric_enabled:
        values.update(
            {
                "FABRIC_VAULT_URL": "https://kv-eda.vault.azure.net/",
                "FABRIC_SIGNING_CERTIFICATE_NAME": "fabric-oauth-signing",
                "FABRIC_CACHE_WRAP_KEY_NAME": "fabric-cache-wrap",
            }
        )
    return "\n".join(f'{name}="{value}"' for name, value in values.items())


def command_value(command: Sequence[str], option: str) -> str:
    index = command.index(option)
    return command[index + 1]


def test_binds_platform_agent_identity_to_core_data_and_sandbox_resources() -> None:
    module = load_script()
    calls: list[tuple[str, ...]] = []

    def runner(command: Sequence[str]) -> str:
        calls.append(tuple(command))
        if tuple(command) == ("azd", "env", "get-values"):
            return azd_values()
        if command[:4] == ["az", "rest", "--method", "GET"]:
            raise AssertionError("agent identity must come from the azd deployment output")
        return ""

    returned_principal = module.bind_hosted_agent_rbac(runner=runner)

    assert returned_principal == PRINCIPAL_ID
    assert (
        "az",
        "role",
        "assignment",
        "create",
        "--assignee-object-id",
        PRINCIPAL_ID,
        "--assignee-principal-type",
        "ServicePrincipal",
        "--role",
        FOUNDRY_USER_ROLE_ID,
        "--scope",
        f"/subscriptions/{SUBSCRIPTION_ID}/resourceGroups/{RESOURCE_GROUP}"
        "/providers/Microsoft.CognitiveServices/accounts/foundry-eda",
        "--output",
        "none",
        "--only-show-errors",
    ) in calls
    assert (
        "az",
        "role",
        "assignment",
        "create",
        "--assignee-object-id",
        PRINCIPAL_ID,
        "--assignee-principal-type",
        "ServicePrincipal",
        "--role",
        BLOB_ROLE_ID,
        "--scope",
        STORAGE_ID,
        "--output",
        "none",
        "--only-show-errors",
    ) in calls

    cosmos_command = next(
        command for command in calls if command[:5] == ("az", "cosmosdb", "sql", "role", "assignment")
    )
    assert command_value(cosmos_command, "--resource-group") == RESOURCE_GROUP
    assert command_value(cosmos_command, "--account-name") == "cosmos-eda"
    assert command_value(cosmos_command, "--principal-id") == PRINCIPAL_ID
    assert command_value(cosmos_command, "--role-definition-id") == COSMOS_ROLE_ID
    assert command_value(cosmos_command, "--scope") == "/"
    UUID(command_value(cosmos_command, "--role-assignment-id"))

    redis_command = next(
        command
        for command in calls
        if command[:4] == ("az", "rest", "--method", "PUT")
        and "accessPolicyAssignments" in command_value(command, "--url")
    )
    assert command_value(redis_command, "--url") == (
        "https://management.azure.com"
        f"{REDIS_ID}/databases/default/accessPolicyAssignments/hostedAgent-222222222222?api-version=2025-07-01"
    )
    assert PRINCIPAL_ID in command_value(redis_command, "--body")

    assert (
        "az",
        "role",
        "assignment",
        "create",
        "--assignee-object-id",
        PRINCIPAL_ID,
        "--assignee-principal-type",
        "ServicePrincipal",
        "--role",
        SANDBOX_ROLE_ID,
        "--scope",
        SANDBOX_ID,
        "--output",
        "none",
        "--only-show-errors",
    ) in calls
    assert ("azd", "env", "set", "HOSTED_AGENT_PRINCIPAL_ID", PRINCIPAL_ID) in calls
    assert all("WORKER_IDENTITY" not in argument for command in calls for argument in command)


def test_fabric_enabled_binds_only_runtime_key_vault_permissions() -> None:
    module = load_script()
    calls: list[tuple[str, ...]] = []

    def runner(command: Sequence[str]) -> str:
        calls.append(tuple(command))
        if tuple(command) == ("azd", "env", "get-values"):
            return azd_values(fabric_enabled=True)
        if command[:4] == ["az", "rest", "--method", "GET"]:
            raise AssertionError("agent identity must come from the azd deployment output")
        return ""

    module.bind_hosted_agent_rbac(runner=runner)

    fabric_assignments = [
        command
        for command in calls
        if command[:4] == ("az", "role", "assignment", "create")
        and command_value(command, "--role").startswith("EDA Fabric")
    ]
    assert {
        (command_value(command, "--role"), command_value(command, "--scope")) for command in fabric_assignments
    } == {
        (
            "EDA Fabric certificate read",
            f"/subscriptions/{SUBSCRIPTION_ID}/resourceGroups/{RESOURCE_GROUP}/providers/Microsoft.KeyVault/vaults/kv-eda",
        ),
        (
            "EDA Fabric OAuth sign",
            f"/subscriptions/{SUBSCRIPTION_ID}/resourceGroups/{RESOURCE_GROUP}"
            "/providers/Microsoft.KeyVault/vaults/kv-eda/keys/fabric-oauth-signing",
        ),
        (
            "EDA Fabric cache wrap and unwrap",
            f"/subscriptions/{SUBSCRIPTION_ID}/resourceGroups/{RESOURCE_GROUP}"
            "/providers/Microsoft.KeyVault/vaults/kv-eda/keys/fabric-cache-wrap",
        ),
    }


def test_missing_agent_identity_fails_before_role_assignment() -> None:
    module = load_script()
    calls: list[tuple[str, ...]] = []

    def runner(command: Sequence[str]) -> str:
        calls.append(tuple(command))
        if tuple(command) == ("azd", "env", "get-values"):
            return azd_values(include_agent_identity=False)
        return ""

    with pytest.raises(ValueError, match="instance identity"):
        module.bind_hosted_agent_rbac(runner=runner)

    assert not any(command[:4] == ("az", "role", "assignment", "create") for command in calls)
    assert not any(command[:5] == ("az", "cosmosdb", "sql", "role", "assignment") for command in calls)
