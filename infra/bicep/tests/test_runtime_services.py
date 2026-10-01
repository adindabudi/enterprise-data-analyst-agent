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

DATA_CONTRIBUTOR_ROLE_ID = "0ad04412-c4d5-4796-b79c-f76d14c8d402"
WORKER_ROLE_ID = "80d0d6b0-f522-40a4-8886-a5a11720c375"


def compiled_resources() -> tuple[Mapping[str, Any], ...]:
    template = build_bicep_template(ROOT / "infra/bicep")
    return tuple(cast(Iterable[Mapping[str, Any]], iter_resources(template)))


def resources_of_type(resources: Iterable[Mapping[str, Any]], resource_type: str) -> tuple[Mapping[str, Any], ...]:
    return tuple(resource for resource in resources if resource.get("type") == resource_type)


def test_managed_redis_uses_entra_tls_and_explicit_private_access_policy() -> None:
    resources = compiled_resources()
    clusters = resources_of_type(resources, "Microsoft.Cache/redisEnterprise")
    databases = resources_of_type(resources, "Microsoft.Cache/redisEnterprise/databases")
    assignments = resources_of_type(
        resources,
        "Microsoft.Cache/redisEnterprise/databases/accessPolicyAssignments",
    )

    assert len(clusters) == 1
    cluster = clusters[0]
    assert cluster["properties"]["minimumTlsVersion"] == "1.2"
    assert "production" in str(cluster["properties"]["highAvailability"])
    public_network_access = str(cluster["properties"]["publicNetworkAccess"])
    assert "productDataPublicAccessEnabled" in public_network_access
    assert "production" not in public_network_access

    assert len(databases) == 1
    database = databases[0]["properties"]
    assert database["accessKeysAuthentication"] == "Disabled"
    assert database["clientProtocol"] == "Encrypted"
    assert database["evictionPolicy"] == "VolatileLRU"
    assert database["port"] == 10000
    assert len(assignments) == 1
    assert "webIdentityPrincipalId" in str(assignments[0]["properties"]["user"])

    redis_private_endpoints = [
        resource
        for resource in resources_of_type(resources, "Microsoft.Network/privateEndpoints")
        if "redis" in str(resource).lower()
    ]
    assert len(redis_private_endpoints) == 1
    private_endpoint_condition = str(redis_private_endpoints[0].get("condition", ""))
    assert "productDataPublicAccessEnabled" in private_endpoint_condition
    assert "production" not in private_endpoint_condition


def test_demo_topology_does_not_deploy_durable_task() -> None:
    resources = compiled_resources()
    schedulers = resources_of_type(resources, "Microsoft.DurableTask/schedulers")
    task_hubs = resources_of_type(resources, "Microsoft.DurableTask/schedulers/taskHubs")

    assert schedulers == ()
    assert task_hubs == ()


def test_demo_topology_does_not_deploy_an_aca_long_job_worker() -> None:
    resources = compiled_resources()
    apps = resources_of_type(resources, "Microsoft.App/containerApps")

    assert len(apps) == 1
    assert "api" in str(apps[0]["name"]).lower()
