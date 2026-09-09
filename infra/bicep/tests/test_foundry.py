from __future__ import annotations

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

COGNITIVE_SERVICES_USER_ROLE_ID = "a97b65f3-24c7-4388-baec-2e87135dc908"
ACR_PULL_ROLE_ID = "7f951dda-4ed3-4680-a7ca-43fe172d538d"
FOUNDRY_USER_ROLE_ID = "53ca6127-db72-4b80-b1b0-d745d6d5456d"


def compiled_resources() -> tuple[Mapping[str, Any], ...]:
    template = build_bicep_template(ROOT / "infra/bicep")
    return tuple(cast(Iterable[Mapping[str, Any]], iter_resources(template)))


def resources_of_type(resources: Iterable[Mapping[str, Any]], resource_type: str) -> tuple[Mapping[str, Any], ...]:
    return tuple(resource for resource in resources if resource.get("type") == resource_type)


def test_foundry_deploys_one_exact_terra_model_with_api_and_worker_access() -> None:
    resources = compiled_resources()
    accounts = resources_of_type(resources, "Microsoft.CognitiveServices/accounts")
    projects = resources_of_type(resources, "Microsoft.CognitiveServices/accounts/projects")
    deployments = resources_of_type(resources, "Microsoft.CognitiveServices/accounts/deployments")
    assignments = resources_of_type(resources, "Microsoft.Authorization/roleAssignments")

    assert len(accounts) == 1
    account = accounts[0]
    assert account["apiVersion"] == "2025-10-01-preview"
    assert account["kind"] == "AIServices"
    assert account["properties"]["allowProjectManagement"] is True
    assert account["properties"]["disableLocalAuth"] is True
    assert account["properties"]["networkInjections"] == [
        {
            "scenario": "agent",
            "subnetArmId": "[parameters('agentSubnetId')]",
            "useMicrosoftManagedNetwork": False,
        }
    ]

    assert len(projects) == 1
    assert projects[0]["apiVersion"] == "2025-10-01-preview"

    assert len(deployments) == 1
    deployment = deployments[0]
    assert deployment["apiVersion"] == "2025-10-01-preview"
    assert deployment["properties"]["model"] == {
        "format": "OpenAI",
        "name": "gpt-5.6-terra",
        "version": "2026-07-09",
    }
    assert "modelProviderData" not in deployment["properties"]
    assert deployment["properties"]["versionUpgradeOption"] == "NoAutoUpgrade"
    assert deployment["properties"]["raiPolicyName"] == "Microsoft.DefaultV2"
    assert "claude" not in str(deployment).lower()
    assert "fallback" not in str(deployment).lower()

    main_source = (ROOT / "infra/bicep/main.bicep").read_text(encoding="utf-8")
    assert "param modelProfile 'gpt-5.6-terra-medium-v1'" in main_source
    assert "claudeOrganizationName" not in main_source
    assert "claudeCountryCode" not in main_source
    assert "claudeIndustry" not in main_source

    cognitive_services_user_assignments = [
        assignment for assignment in assignments if COGNITIVE_SERVICES_USER_ROLE_ID in str(assignment)
    ]
    assert len(cognitive_services_user_assignments) == 2
    serialized_assignments = "\n".join(str(assignment) for assignment in cognitive_services_user_assignments)
    assert "webIdentityPrincipalId" in serialized_assignments
    assert "workerIdentityPrincipalId" in serialized_assignments
    assert all("account" in str(assignment) for assignment in cognitive_services_user_assignments)


def test_foundry_outputs_expose_project_and_account_endpoints_with_deployment_name() -> None:
    template = build_bicep_template(ROOT / "infra/bicep")
    outputs = cast(Mapping[str, Mapping[str, Any]], template["outputs"])

    for required_output in (
        "AZURE_AI_ACCOUNT_NAME",
        "AZURE_AI_MODEL_DEPLOYMENT_NAME",
        "AZURE_AI_PROJECT_ID",
        "AZURE_AI_PROJECT_NAME",
        "AZURE_CONTAINER_REGISTRY_ENDPOINT",
        "AZURE_CONTAINER_REGISTRY_RESOURCE_ID",
        "AZURE_OPENAI_ENDPOINT",
        "FOUNDRY_PROJECT_ENDPOINT",
    ):
        assert required_output in outputs
    assert "projectEndpoint" in outputs
    assert "foundryResourceEndpoint" in outputs
    assert outputs["foundryResourceEndpoint"]["value"] == ("[reference('foundry').outputs.resourceEndpoint.value]")
    assert "modelDeploymentName" in outputs
    assert "modelProfile" in outputs
    assert all(
        forbidden not in output_name.lower()
        for output_name in outputs
        for forbidden in ("key", "secret", "token", "connectionstring", "password", "sas")
    )


def test_foundry_project_identity_can_pull_the_hosted_agent_image() -> None:
    foundry = (ROOT / "infra/bicep/modules/foundry.bicep").read_text(encoding="utf-8")
    registry = (ROOT / "infra/bicep/modules/container-registry.bicep").read_text(encoding="utf-8")
    main = (ROOT / "infra/bicep/main.bicep").read_text(encoding="utf-8")

    assert "output projectPrincipalId string = project.identity.principalId" in foundry
    assert "param foundryProjectPrincipalId string" in registry
    assert "resource foundryProjectAcrPull" in registry
    assert "principalId: foundryProjectPrincipalId" in registry
    assert ACR_PULL_ROLE_ID in registry
    assert "foundryProjectPrincipalId: foundry.outputs.projectPrincipalId" in main


def test_foundry_project_identity_can_use_the_account_runtime() -> None:
    foundry = (ROOT / "infra/bicep/modules/foundry.bicep").read_text(encoding="utf-8")

    assert "resource projectFoundryUser" in foundry
    assert "principalId: project.identity.principalId" in foundry
    assert FOUNDRY_USER_ROLE_ID in foundry
    assert "scope: account" in foundry
