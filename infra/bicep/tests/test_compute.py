from __future__ import annotations

import importlib.util
import json
import re
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


def resources_of_type(resources: Iterable[Mapping[str, Any]], resource_type: str) -> tuple[Mapping[str, Any], ...]:
    return tuple(resource for resource in resources if resource.get("type") == resource_type)


def environment_names(container: Mapping[str, Any]) -> set[str]:
    environment = container["properties"]["template"]["containers"][0]["env"]
    if isinstance(environment, list):
        return {str(item["name"]) for item in environment}
    return set(re.findall(r"createObject\('name', '([^']+)'", str(environment)))


def test_container_apps_use_one_api_and_digest_pinned_scheduled_cleanup() -> None:
    template = build_bicep_template(ROOT / "infra/bicep")
    resources = tuple(cast(Iterable[Mapping[str, Any]], iter_resources(template)))
    environments = resources_of_type(resources, "Microsoft.App/managedEnvironments")
    apps = resources_of_type(resources, "Microsoft.App/containerApps")
    jobs = resources_of_type(resources, "Microsoft.App/jobs")

    assert len(environments) == 1
    assert len(apps) == 1
    assert len(jobs) == 2

    app_logs = environments[0]["properties"]["appLogsConfiguration"]
    assert app_logs["destination"] == "log-analytics"
    assert app_logs["logAnalyticsConfiguration"] == {
        "customerId": "[parameters('logAnalyticsWorkspaceId')]",
        "sharedKey": "[parameters('logAnalyticsSharedKey')]",
    }

    api = next(app for app in apps if "api" in str(app["name"]).lower())
    cleanup = next(job for job in jobs if job["properties"]["configuration"]["triggerType"] == "Schedule")
    acceptance = next(job for job in jobs if job["properties"]["configuration"]["triggerType"] == "Manual")
    assert "fabricEnabled" in str(acceptance.get("condition", ""))
    api_template = api["properties"]["template"]

    assert "azd-service-name" in str(api["tags"])
    assert "api" in str(api["tags"])
    assert api["properties"]["configuration"]["ingress"] == {
        "external": True,
        "targetPort": 8000,
        "transport": "auto",
        "stickySessions": {"affinity": "none"},
    }
    assert len(api_template["containers"][0]["probes"]) == 3
    assert {
        probe.get("httpGet", {}).get("path")
        for probe in api_template["containers"][0]["probes"]
        if probe["type"] in {"Startup", "Readiness"}
    } == {"/health/platform-ready"}
    assert api_template["containers"][0]["image"] == "[parameters('apiImage')]"
    assert "@sha256:" in template["parameters"]["apiImage"]["defaultValue"]
    assert "@sha256:" in template["parameters"]["workerImage"]["defaultValue"]
    assert cleanup["properties"]["configuration"]["triggerType"] == "Schedule"
    assert cleanup["properties"]["configuration"]["scheduleTriggerConfig"]["cronExpression"] == "0 2 * * *"
    cleanup_container = cleanup["properties"]["template"]["containers"][0]
    assert cleanup_container["command"] == ["eda-worker", "cleanup", "--before", "now", "--limit", "100"]
    assert cleanup["properties"]["template"]["containers"][0]["image"] == "[parameters('workerImage')]"
    cleanup_environment = {item["name"]: item.get("value") for item in cleanup_container["env"]}
    assert cleanup_environment["EDA_COSMOS_ENDPOINT"] == "[parameters('cosmosEndpoint')]"
    assert cleanup_environment["EDA_COSMOS_DATABASE"] == "[parameters('cosmosDatabase')]"
    assert cleanup_environment["EDA_COSMOS_WORKSPACE_CONTAINER"] == "[parameters('cosmosWorkspaceContainer')]"
    assert cleanup_environment["EDA_BLOB_ACCOUNT_URL"] == "[parameters('blobAccountUrl')]"
    assert cleanup_environment["EDA_BLOB_SESSIONS_CONTAINER"] == "sessions"


def test_api_receives_hosted_agent_dispatch_settings() -> None:
    template = build_bicep_template(ROOT / "infra/bicep")
    resources = tuple(cast(Iterable[Mapping[str, Any]], iter_resources(template)))
    apps = resources_of_type(resources, "Microsoft.App/containerApps")
    api = next(app for app in apps if "api" in str(app["name"]).lower())
    api_env = environment_names(api)

    required = {
        "EDA_APP_ENV",
        "EDA_MANAGED_IDENTITY_CLIENT_ID",
        "EDA_COSMOS_ENDPOINT",
        "EDA_COSMOS_DATABASE",
        "EDA_COSMOS_WORKSPACE_CONTAINER",
        "EDA_COSMOS_AUTH_CONTAINER",
        "EDA_COSMOS_RUNTIME_CONTAINER",
        "EDA_BLOB_ACCOUNT_URL",
        "EDA_BLOB_QUARANTINE_CONTAINER",
        "EDA_BLOB_SESSIONS_CONTAINER",
        "EDA_REDIS_URL",
        "EDA_FOUNDRY_PROJECT_ENDPOINT",
        "EDA_FOUNDRY_MODEL_DEPLOYMENT",
        "EDA_MODEL_PROFILE",
        "EDA_FOUNDRY_HOSTING",
        "EDA_DEPLOYMENT_ID",
        "EDA_WORKER_IMAGE_DIGEST",
        "EDA_COSMOS_FABRIC_AUTH_CONTAINER",
    }
    assert required <= api_env
    assert {"EDA_DTS_ENDPOINT", "EDA_DTS_TASKHUB"}.isdisjoint(api_env)
    assert {"EDA_HOSTED_AGENT_NAME", "EDA_HOSTED_AGENT_ENABLED"} <= api_env
    assert {"EDA_PUBLIC_ORIGIN", "EDA_ENTRA_TENANT_ID", "EDA_ENTRA_CLIENT_ID", "EDA_COOKIE_SECURE"} <= api_env


def test_provision_uses_pinned_application_images_from_azd_environment() -> None:
    parameters = json.loads((ROOT / "infra/bicep/main.parameters.json").read_text(encoding="utf-8"))["parameters"]

    assert parameters["apiImage"]["value"].startswith("${EDA_API_IMAGE=example.azurecr.io/eda-api@sha256:")
    assert parameters["workerImage"]["value"].startswith("${EDA_WORKER_IMAGE=example.azurecr.io/eda-worker@sha256:")


def test_bicep_exports_the_document_contract_deployment_identity() -> None:
    template = build_bicep_template(ROOT / "infra/bicep")

    assert template["outputs"]["EDA_DEPLOYMENT_ID"]["value"] == "[variables('configurationHash')]"


def test_bicep_output_aliases_match_deployment_hook_inputs() -> None:
    outputs = build_bicep_template(ROOT / "infra/bicep")["outputs"]
    spec = importlib.util.spec_from_file_location("sync_bicep_outputs", ROOT / "scripts/sync-bicep-outputs.py")
    assert spec is not None and spec.loader is not None
    synchronizer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(synchronizer)
    updates = synchronizer.environment_updates({name: value["value"] for name, value in outputs.items()})
    aliases = {
        "API_URL": "appUrl",
        "API_APP_ID": "apiAppId",
        "CLEANUP_JOB_ID": "cleanupJobId",
        "FABRIC_ACCEPTANCE_JOB_ID": "fabricAcceptanceJobId",
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

    assert set(aliases) == set(updates)
    for alias, original in aliases.items():
        assert updates[alias] == str(outputs[original]["value"]), alias
    assert outputs["AZURE_RESOURCE_GROUP"]["value"] == "[parameters('resourceGroupName')]"
