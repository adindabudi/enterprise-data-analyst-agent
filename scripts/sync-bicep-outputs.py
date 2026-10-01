from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Mapping
from typing import cast

OUTPUT_ALIASES = {
    "API_URL": "appUrl",
    "API_APP_ID": "apiAppId",
    "CLEANUP_JOB_ID": "cleanupJobId",
    "DEPLOYMENT_ID": "deploymentId",
    "CONTAINER_REGISTRY_ID": "containerRegistryId",
    "CONTAINER_REGISTRY_LOGIN_SERVER": "containerRegistryLoginServer",
    "COSMOS_ACCOUNT_ID": "cosmosAccountId",
    "COSMOS_ENDPOINT": "cosmosEndpoint",
    "STORAGE_ACCOUNT_ID": "storageAccountId",
    "STORAGE_BLOB_ENDPOINT": "storageBlobEndpoint",
    "REDIS_CLUSTER_ID": "redisClusterId",
    "REDIS_HOSTNAME": "redisHostname",
    "REDIS_PORT": "redisPort",
    "WEB_IDENTITY_PRINCIPAL_ID": "webIdentityPrincipalId",
    "WORKER_IDENTITY_CLIENT_ID": "workerIdentityClientId",
    "WORKER_IDENTITY_PRINCIPAL_ID": "workerIdentityPrincipalId",
    "SESSION_INIT_IDENTITY_ID": "sessionInitIdentityId",
    "SANDBOX_SUBNET_ID": "sandboxSubnetId",
    "FOUNDRY_AGENT_SUBNET_ID": "foundryAgentSubnetId",
    "CONTAINER_APPS_ENVIRONMENT_ID": "containerAppsEnvironmentId",
    "EDA_FOUNDRY_PROJECT_ENDPOINT": "projectEndpoint",
    "EDA_FOUNDRY_RESOURCE_ENDPOINT": "foundryResourceEndpoint",
    "EDA_FOUNDRY_MODEL_DEPLOYMENT": "modelDeploymentName",
}


def environment_updates(values: Mapping[str, object]) -> dict[str, str]:
    updates: dict[str, str] = {}
    for alias, original in OUTPUT_ALIASES.items():
        value = values.get(original)
        if not isinstance(value, (str, int)) or isinstance(value, bool):
            raise ValueError(f"missing or invalid Bicep output: {original}")
        if not str(value):
            raise ValueError(f"empty Bicep output: {original}")
        updates[alias] = str(value)
    return updates


def main() -> int:
    azd = shutil.which("azd")
    if azd is None:
        print("FAIL: azd is required")
        return 1
    try:
        result = subprocess.run(  # noqa: S603
            [azd, "env", "get-values", "--output", "json"], check=True, capture_output=True, text=True
        )
        document = cast(object, json.loads(result.stdout))
        if not isinstance(document, dict):
            raise ValueError("azd environment must be an object")
        updates = environment_updates(cast(dict[str, object], document))
        for name, value in updates.items():
            subprocess.run(  # noqa: S603
                [azd, "env", "set", name, value], check=True, capture_output=True, text=True
            )
    except (ValueError, OSError, subprocess.CalledProcessError):
        print("FAIL: unable to synchronize Bicep outputs; verify provisioning outputs and azd environment")
        return 1
    print(f"PASS: synchronized {len(updates)} Bicep output aliases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
