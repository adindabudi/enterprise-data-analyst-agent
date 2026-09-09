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


def compiled_resources() -> tuple[Mapping[str, Any], ...]:
    template = build_bicep_template(ROOT / "infra/bicep")
    return tuple(cast(Iterable[Mapping[str, Any]], iter_resources(template)))


def resources_of_type(resource_type: str) -> tuple[Mapping[str, Any], ...]:
    return tuple(resource for resource in compiled_resources() if resource.get("type") == resource_type)


def test_fabric_disabled_is_the_default_and_every_added_resource_is_conditional() -> None:
    main = (ROOT / "infra/bicep/main.bicep").read_text(encoding="utf-8")
    assert "param fabricEnabled bool = false" in main
    assert "module fabricAuth 'modules/fabric-auth.bicep' = if (fabricEnabled)" in main

    vaults = resources_of_type("Microsoft.KeyVault/vaults")
    fabric_jobs = [
        resource for resource in resources_of_type("Microsoft.App/jobs") if "fabric-acceptance" in str(resource)
    ]
    fabric_containers = [
        resource
        for resource in resources_of_type("Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers")
        if "fabricAuth" in str(resource)
    ]
    assert len(vaults) == 1
    assert len(fabric_jobs) == 1
    assert len(fabric_containers) == 1
    assert all(
        any(flag in str(resource.get("condition", "")) for flag in ("fabricEnabled", "enabled"))
        for resource in (*vaults, *fabric_jobs, *fabric_containers)
    )


def test_fabric_vault_has_only_declared_crypto_data_actions() -> None:
    source = (ROOT / "infra/bicep/modules/fabric-auth.bicep").read_text(encoding="utf-8")
    required = {
        "Microsoft.KeyVault/vaults/certificates/read",
        "Microsoft.KeyVault/vaults/certificates/create/action",
        "Microsoft.KeyVault/vaults/keys/sign/action",
        "Microsoft.KeyVault/vaults/keys/wrap/action",
        "Microsoft.KeyVault/vaults/keys/unwrap/action",
    }
    for action in required:
        assert action in source
    for forbidden in (
        "Microsoft.KeyVault/vaults/secrets/read",
        "Microsoft.KeyVault/vaults/secrets/write",
        "Microsoft.KeyVault/vaults/keys/decrypt/action",
        "Microsoft.KeyVault/vaults/keys/export/action",
    ):
        assert forbidden not in source
    assert "enableRbacAuthorization: true" in source
    assert "enablePurgeProtection: true" in source
    assert "softDeleteRetentionInDays: 90" in source
    assert "cleanupPreference: 'OnSuccess'" in source
    assert "az keyvault key create" in source
    assert "az keyvault certificate create" in source


def test_fabric_vault_is_private_and_uses_private_dns() -> None:
    fabric = (ROOT / "infra/bicep/modules/fabric-auth.bicep").read_text(encoding="utf-8")
    network = (ROOT / "infra/bicep/modules/network.bicep").read_text(encoding="utf-8")
    main = (ROOT / "infra/bicep/main.bicep").read_text(encoding="utf-8")

    assert "publicNetworkAccess: 'Disabled'" in fabric
    assert "defaultAction: 'Deny'" in fabric
    assert "groupIds: [\n            'vault'" in fabric
    assert "Microsoft.Network/privateEndpoints/privateDnsZoneGroups" in fabric
    assert "param certificateProvisioningEnabled bool" in fabric
    assert "param signingCertificateReady bool" in fabric
    assert "privatelink.vaultcore.azure.net" in network
    assert "output keyVaultPrivateDnsZoneId" in network
    assert "privateDnsZoneId: network.outputs.keyVaultPrivateDnsZoneId" in main


def test_private_vault_artifacts_are_bootstrapped_through_the_vnet() -> None:
    source = (ROOT / "infra/bicep/modules/fabric-auth.bicep").read_text(encoding="utf-8")

    assert resources_of_type("Microsoft.KeyVault/vaults/keys") == ()
    assert "Microsoft.KeyVault/vaults/keys/create/action" in source
    assert source.count("Microsoft.KeyVault/vaults/keys/read") == 1
    assert "resource cacheWrapKey 'Microsoft.KeyVault/vaults/keys@2024-11-01' existing" in source
    for assignment in ("webCacheCrypto", "workerCacheCrypto", "acceptanceCacheWrap"):
        assert (
            f"resource {assignment} 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (enabled && certificateReady)"
            in source
        )


def test_enabled_runtime_receives_only_nonsecret_fabric_configuration() -> None:
    source = (ROOT / "infra/bicep/modules/container-apps.bicep").read_text(encoding="utf-8")
    main = (ROOT / "infra/bicep/main.bicep").read_text(encoding="utf-8")
    parameters = (ROOT / "infra/bicep/main.parameters.json").read_text(encoding="utf-8")
    for name in (
        "FABRIC_ENABLED",
        "FABRIC_PROVIDER",
        "FABRIC_TENANT_ID",
        "FABRIC_CLIENT_ID",
        "FABRIC_KEY_VAULT_URL",
        "FABRIC_SIGNING_CERTIFICATE_NAME",
        "FABRIC_CACHE_WRAP_KEY_NAME",
        "FABRIC_SEMANTIC_MODELS_JSON",
        "FABRIC_ONTOLOGIES_JSON",
        "EDA_DEPLOYMENT_ID",
        "EDA_COSMOS_FABRIC_AUTH_CONTAINER",
    ):
        assert f"name: '{name}'" in source
    for forbidden in ("FABRIC_CLIENT_SECRET", "FABRIC_ACCESS_TOKEN", "FABRIC_REFRESH_TOKEN", "FABRIC_MCP_URL"):
        assert forbidden not in source

    resources = compiled_resources()
    apps = tuple(resource for resource in resources if resource.get("type") == "Microsoft.App/containerApps")
    api = next(resource for resource in apps if "apiName" in str(resource["name"]))
    api_environment = str(api["properties"]["template"]["containers"][0]["env"])
    assert "FABRIC_SEMANTIC_MODELS_JSON" not in api_environment
    assert "FABRIC_ONTOLOGIES_JSON" in api_environment
    manifest = (ROOT / "azure.yaml").read_text(encoding="utf-8")
    assert "FABRIC_ENABLED: ${FABRIC_ENABLED=false}" in manifest
    assert "output FABRIC_KEY_VAULT_URL string" in main
    assert "FABRIC_RUNTIME_ENABLED" not in manifest
    assert "fabricRuntimeEnabled" not in main
    assert "FABRIC_RUNTIME_ENABLED" not in parameters


def test_acceptance_job_is_manual_nonnetworked_and_uses_exact_worker_image() -> None:
    source = (ROOT / "infra/bicep/modules/container-apps.bicep").read_text(encoding="utf-8")
    assert "var fabricAcceptanceName = 'fabric-acc-${environmentName}-${suffix}'" in source
    resources = resources_of_type("Microsoft.App/jobs")
    [job] = [resource for resource in resources if "fabric-acceptance" in str(resource)]
    assert job["properties"]["configuration"]["triggerType"] == "Manual"
    assert job["properties"]["configuration"]["manualTriggerConfig"] == {
        "parallelism": 1,
        "replicaCompletionCount": 1,
    }
    assert "ingress" not in str(job).lower()
    assert "workerImage" in str(job["properties"]["template"]["containers"][0]["image"])
    assert "FABRIC_ACCEPTANCE_MODE" in str(job)
    assert "eda-worker" in str(job)
    assert "accept-fabric" in str(job)
    assert "fabricProvider" in str(job["properties"]["template"]["containers"][0]["command"])


def test_provider_and_catalog_parameters_are_mutually_selectable_without_new_resources() -> None:
    main = (ROOT / "infra/bicep/main.bicep").read_text(encoding="utf-8")
    parameters = (ROOT / "infra/bicep/main.parameters.json").read_text(encoding="utf-8")
    assert "'semantic_model'" in main
    assert "'ontology'" in main
    assert "param fabricProvider string = ''" in main
    assert "@secure()\nparam fabricOntologiesJson string = ''" in main
    assert '"${FABRIC_PROVIDER=}"' in parameters
    assert '"${FABRIC_SEMANTIC_MODELS_JSON=}"' in parameters
    assert '"${FABRIC_ONTOLOGIES_JSON=}"' in parameters
    assert "={}}" not in parameters
    assert len(resources_of_type("Microsoft.KeyVault/vaults")) == 1
    assert (
        len([resource for resource in resources_of_type("Microsoft.App/jobs") if "fabric-acceptance" in str(resource)])
        == 1
    )
