from __future__ import annotations

import json
import re
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from urllib.parse import urlparse
from uuid import NAMESPACE_URL, UUID, uuid5

CommandRunner = Callable[[Sequence[str]], str]

BLOB_DATA_CONTRIBUTOR_ROLE_ID = "ba92f5b4-2d11-453d-a403-e96b0029c9fe"
COSMOS_DATA_CONTRIBUTOR_ROLE_ID = "00000000-0000-0000-0000-000000000002"
SANDBOX_DATA_OWNER_ROLE_ID = "c24cf47c-5077-412d-a19c-45202126392c"
FOUNDRY_USER_ROLE_ID = "53ca6127-db72-4b80-b1b0-d745d6d5456d"
REDIS_API_VERSION = "2025-07-01"
SAFE_RESOURCE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def run_command(command: Sequence[str]) -> str:
    completed = subprocess.run(  # noqa: S603 - command is assembled from fixed executable and option lists.
        list(command),
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        details = completed.stderr.strip() or completed.stdout.strip() or "command failed"
        raise RuntimeError(details)
    return completed.stdout.strip()


def parse_azd_values(raw: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in raw.splitlines():
        if not line:
            continue
        name, separator, encoded_value = line.partition("=")
        if not separator or not name:
            raise ValueError("azd environment output is malformed")
        if name in values:
            raise ValueError(f"azd environment contains duplicate value: {name}")
        value: object
        if encoded_value.startswith('"'):
            try:
                value = json.loads(encoded_value)
            except json.JSONDecodeError as error:
                raise ValueError(f"azd environment value is malformed: {name}") from error
        else:
            value = encoded_value
        if not isinstance(value, str):
            raise ValueError(f"azd environment value must be a string: {name}")
        values[name] = value
    return values


def required(values: Mapping[str, str], name: str) -> str:
    value = values.get(name, "").strip()
    if not value:
        raise ValueError(f"required azd environment value is missing: {name}")
    return value


def validate_uuid(value: str, label: str) -> str:
    try:
        parsed = UUID(value)
    except ValueError as error:
        raise ValueError(f"{label} must be a UUID") from error
    if str(parsed) != value.lower():
        raise ValueError(f"{label} must be a canonical UUID")
    return str(parsed)


def resource_group_and_name(resource_id: str, *, provider: str, resource_type: str) -> tuple[str, str]:
    segments = resource_id.strip("/").split("/")
    expected_length = 8
    if len(segments) != expected_length:
        raise ValueError(f"invalid {resource_type} resource ID")
    labels = [segment.lower() for segment in segments]
    if (
        labels[0] != "subscriptions"
        or labels[2] != "resourcegroups"
        or labels[4] != "providers"
        or labels[5] != provider.lower()
        or labels[6] != resource_type.lower()
    ):
        raise ValueError(f"invalid {resource_type} resource ID")
    resource_group = segments[3]
    resource_name = segments[7]
    if not SAFE_RESOURCE_NAME.fullmatch(resource_group) or not SAFE_RESOURCE_NAME.fullmatch(resource_name):
        raise ValueError(f"invalid {resource_type} resource ID")
    return resource_group, resource_name


def role_assignment_command(principal_id: str, role: str, scope: str) -> list[str]:
    return [
        "az",
        "role",
        "assignment",
        "create",
        "--assignee-object-id",
        principal_id,
        "--assignee-principal-type",
        "ServicePrincipal",
        "--role",
        role,
        "--scope",
        scope,
        "--output",
        "none",
        "--only-show-errors",
    ]


def redis_assignment_name(prefix: str, principal_id: str) -> str:
    return f"{prefix}-{principal_id.replace('-', '')[:12]}"


def bind_hosted_agent_rbac(*, runner: CommandRunner = run_command) -> str:
    values = parse_azd_values(runner(["azd", "env", "get-values"]))
    subscription_id = validate_uuid(required(values, "AZURE_SUBSCRIPTION_ID"), "AZURE_SUBSCRIPTION_ID")
    resource_group = required(values, "AZURE_RESOURCE_GROUP")
    principal_id = values.get("AGENT_LONG_JOB_INSTANCE_IDENTITY_PRINCIPAL_ID", "").strip()
    if not principal_id:
        raise ValueError("Hosted Agent instance identity is unavailable from the azd deployment output")
    principal_id = validate_uuid(principal_id, "Hosted Agent instance identity")

    foundry_account = required(values, "AZURE_AI_ACCOUNT_NAME")
    if not SAFE_RESOURCE_NAME.fullmatch(foundry_account):
        raise ValueError("AZURE_AI_ACCOUNT_NAME is invalid")
    foundry_account_id = (
        f"/subscriptions/{subscription_id}/resourceGroups/{resource_group}"
        f"/providers/Microsoft.CognitiveServices/accounts/{foundry_account}"
    )
    runner(role_assignment_command(principal_id, FOUNDRY_USER_ROLE_ID, foundry_account_id))

    storage_id = required(values, "STORAGE_ACCOUNT_ID")
    storage_resource_group, _ = resource_group_and_name(
        storage_id,
        provider="Microsoft.Storage",
        resource_type="storageAccounts",
    )
    if storage_resource_group.lower() != resource_group.lower():
        raise ValueError("STORAGE_ACCOUNT_ID belongs to a different resource group")
    runner(role_assignment_command(principal_id, BLOB_DATA_CONTRIBUTOR_ROLE_ID, storage_id))

    cosmos_id = required(values, "COSMOS_ACCOUNT_ID")
    cosmos_resource_group, cosmos_account = resource_group_and_name(
        cosmos_id,
        provider="Microsoft.DocumentDB",
        resource_type="databaseAccounts",
    )
    if cosmos_resource_group.lower() != resource_group.lower():
        raise ValueError("COSMOS_ACCOUNT_ID belongs to a different resource group")
    cosmos_assignment_id = str(
        uuid5(
            NAMESPACE_URL,
            f"{cosmos_id.lower()}|{principal_id}|{COSMOS_DATA_CONTRIBUTOR_ROLE_ID}|/",
        )
    )
    runner(
        [
            "az",
            "cosmosdb",
            "sql",
            "role",
            "assignment",
            "create",
            "--resource-group",
            cosmos_resource_group,
            "--account-name",
            cosmos_account,
            "--principal-id",
            principal_id,
            "--role-definition-id",
            COSMOS_DATA_CONTRIBUTOR_ROLE_ID,
            "--scope",
            "/",
            "--role-assignment-id",
            cosmos_assignment_id,
            "--output",
            "none",
            "--only-show-errors",
        ]
    )

    redis_id = required(values, "REDIS_CLUSTER_ID")
    redis_resource_group, _ = resource_group_and_name(
        redis_id,
        provider="Microsoft.Cache",
        resource_type="redisEnterprise",
    )
    if redis_resource_group.lower() != resource_group.lower():
        raise ValueError("REDIS_CLUSTER_ID belongs to a different resource group")
    redis_body = json.dumps(
        {
            "properties": {
                "accessPolicyName": "default",
                "user": {"objectId": principal_id},
            }
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    redis_assignment = redis_assignment_name("hostedAgent", principal_id)
    runner(
        [
            "az",
            "rest",
            "--method",
            "PUT",
            "--url",
            "https://management.azure.com"
            f"{redis_id}/databases/default/accessPolicyAssignments/{redis_assignment}?api-version={REDIS_API_VERSION}",
            "--body",
            redis_body,
            "--output",
            "none",
            "--only-show-errors",
        ]
    )

    sandbox_resource_group = required(values, "EDA_SANDBOX_RESOURCE_GROUP")
    sandbox_group = required(values, "EDA_SANDBOX_GROUP")
    if not SAFE_RESOURCE_NAME.fullmatch(sandbox_resource_group) or not SAFE_RESOURCE_NAME.fullmatch(sandbox_group):
        raise ValueError("Sandbox group resource coordinates are invalid")
    sandbox_id = (
        f"/subscriptions/{subscription_id}/resourceGroups/{sandbox_resource_group}"
        f"/providers/Microsoft.App/sandboxGroups/{sandbox_group}"
    )
    runner(role_assignment_command(principal_id, SANDBOX_DATA_OWNER_ROLE_ID, sandbox_id))

    fabric_enabled = values.get("FABRIC_ENABLED", "false").lower()
    if fabric_enabled not in {"true", "false"}:
        raise ValueError("FABRIC_ENABLED must be true or false")
    if fabric_enabled == "true":
        bind_fabric_roles(values, principal_id, subscription_id, resource_group, runner)

    runner(["azd", "env", "set", "HOSTED_AGENT_PRINCIPAL_ID", principal_id])
    return principal_id


def bind_fabric_roles(
    values: Mapping[str, str],
    principal_id: str,
    subscription_id: str,
    resource_group: str,
    runner: CommandRunner,
) -> None:
    vault_url = urlparse(required(values, "FABRIC_VAULT_URL"))
    if vault_url.scheme != "https" or not vault_url.hostname or not vault_url.hostname.endswith(".vault.azure.net"):
        raise ValueError("FABRIC_VAULT_URL must be an Azure Key Vault HTTPS endpoint")
    vault_name = vault_url.hostname.removesuffix(".vault.azure.net")
    if not SAFE_RESOURCE_NAME.fullmatch(vault_name):
        raise ValueError("FABRIC_VAULT_URL has an invalid vault name")
    certificate_name = values.get("FABRIC_SIGNING_CERTIFICATE_NAME", "fabric-oauth-signing")
    cache_key_name = values.get("FABRIC_CACHE_WRAP_KEY_NAME", "fabric-cache-wrap")
    if not SAFE_RESOURCE_NAME.fullmatch(certificate_name) or not SAFE_RESOURCE_NAME.fullmatch(cache_key_name):
        raise ValueError("Fabric Key Vault object names are invalid")

    vault_scope = (
        f"/subscriptions/{subscription_id}/resourceGroups/{resource_group}"
        f"/providers/Microsoft.KeyVault/vaults/{vault_name}"
    )
    assignments = (
        ("EDA Fabric certificate read", vault_scope),
        ("EDA Fabric OAuth sign", f"{vault_scope}/keys/{certificate_name}"),
        ("EDA Fabric cache wrap and unwrap", f"{vault_scope}/keys/{cache_key_name}"),
    )
    for role, scope in assignments:
        runner(role_assignment_command(principal_id, role, scope))


def main() -> int:
    try:
        principal_id = bind_hosted_agent_rbac()
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print(f"Hosted Agent RBAC bound: principal={principal_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
