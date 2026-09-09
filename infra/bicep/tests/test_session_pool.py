from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MAIN = ROOT / "infra/bicep/main.bicep"
MODULE = ROOT / "infra/bicep/modules/sandbox-group.bicep"
DEMO_PARAMETERS = ROOT / "infra/bicep/parameters/demo.bicepparam"
PRODUCTION_PARAMETERS = ROOT / "infra/bicep/parameters/production.bicepparam"
BUILD_SCRIPT = ROOT / "scripts/build-sandbox-image.sh"
DEPLOY_SCRIPT = ROOT / "scripts/deploy-sandbox-group.sh"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_sandbox_group_is_scale_to_zero_and_vnet_integrated() -> None:
    module = read(MODULE)

    assert "targetScope = 'resourceGroup'" in module
    assert "Microsoft.App/sandboxGroups@2026-02-01-preview" in module
    assert "defaultCpu: '2'" in module
    assert "defaultMemory: '4Gi'" in module
    assert "defaultDisk: '40Gi'" in module
    assert "maxSandboxCount: 10" in module
    assert "defaultTimeoutSeconds: 300" in module
    assert "Microsoft.App/sandboxGroups/vnetConnections@2026-02-01-preview" in module
    assert "subnetId: sandboxSubnetId" in module
    assert "readySessionInstances" not in module
    assert "sessionPools" not in module


def test_demo_profile_scales_compute_to_zero() -> None:
    main = read(MAIN)
    demo = read(DEMO_PARAMETERS)
    production = read(PRODUCTION_PARAMETERS)

    assert "param apiMinReplicas int = 0" in main
    assert "param apiMinReplicas = 0" in demo
    assert "param apiMinReplicas = 3" in production
    assert "workerMinReplicas" not in main
    assert "workerMinReplicas" not in demo
    assert "workerMinReplicas" not in production
    assert "readySessions" not in main
    assert "readySessions" not in demo
    assert "readySessions" not in production


def test_subscription_topology_declares_the_sandbox_group_post_provision() -> None:
    main = read(MAIN)

    assert "module sandboxGroup" not in main
    assert "resource referenceTopology" in main
    assert "if (false)" in main
    assert "Microsoft.App/sandboxGroups" in main


def test_sandbox_group_defers_data_owner_to_the_hosted_agent_binder() -> None:
    module = read(MODULE)
    binder = read(ROOT / "scripts/bind-hosted-agent-rbac.py")
    deploy = read(DEPLOY_SCRIPT)

    assert "workerIdentityPrincipalId" not in module
    assert "param provisioningPrincipalId string" in module
    assert "principalId: provisioningPrincipalId" in module
    assert "Microsoft.Authorization/roleAssignments" in module
    assert "c24cf47c-5077-412d-a19c-45202126392c" in binder
    assert "c24cf47c-5077-412d-a19c-45202126392c" in module
    assert 'provisioningPrincipalId="$provisioning_principal_id"' in deploy
    assert "Contributor" not in module
    assert "'Owner'" not in module


def test_sandbox_build_and_group_deploy_fail_closed_on_mutable_images() -> None:
    build_script = read(BUILD_SCRIPT)
    deploy_script = read(DEPLOY_SCRIPT)

    assert "set -eu" in build_script
    assert "az acr build" in build_script
    assert "az acr manifest show-metadata" in build_script
    assert "sandbox-image.json" in build_script
    assert "@sha256:" in build_script
    assert "azd env get-values" in build_script
    assert '--build-arg "SOURCE_REVISION=${source_revision}"' in build_script
    assert "--password" not in build_script
    assert "set -eu" in deploy_script
    assert "sandbox-image.json" in deploy_script
    assert "@sha256:" in deploy_script
    assert "azd env get-values" in deploy_script
    assert "az deployment group create" in deploy_script
    assert "sandboxGroupId" in deploy_script
    assert "sandboxDiskImageId" in deploy_script
    assert "azd env set" in deploy_script
    assert "--password" not in deploy_script
