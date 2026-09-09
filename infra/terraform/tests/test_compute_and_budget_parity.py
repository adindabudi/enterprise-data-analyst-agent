from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_container_apps_keep_one_api_and_gated_jobs() -> None:
    source = read("infra/terraform/modules/container_apps/main.tf")

    assert "Microsoft.App/managedEnvironments@2026-01-01" in source
    assert "Microsoft.App/containerApps@2026-01-01" in source
    assert "targetPort" in source and "8000" in source
    assert 'resource "azapi_resource" "worker"' not in source
    assert 'acceptance_name       = "fabric-acc-${var.environment_name}-${var.suffix}"' in source
    assert "count                     = var.deploy_image_dependent_resources ? 1 : 0" in source
    assert 'cronExpression         = "0 2 * * *"' in source
    assert '["eda-worker", "cleanup", "--before", "now", "--limit", "100"]' in source
    assert "count                     = var.deploy_image_dependent_resources && var.fabric_enabled ? 1 : 0" in source
    assert '["eda-worker", "accept-fabric", "--provider", var.fabric_provider]' in source


def test_sandbox_group_scales_to_zero_and_uses_a_dedicated_subnet() -> None:
    source = read("infra/terraform/modules/sandbox_group/main.tf")

    assert "Microsoft.App/sandboxGroups@2026-02-01-preview" in source
    assert "Microsoft.App/sandboxGroups/vnetConnections@2026-02-01-preview" in source
    assert "defaultTimeoutSeconds = 300" in source
    assert "maxSandboxCount       = 10" in source
    assert "readySessionInstances" not in source


def test_budget_module_and_tfvars_match_bicep_profiles() -> None:
    budgets = read("infra/terraform/modules/budgets_alerts/main.tf")
    demo = read("infra/terraform/parameters/demo.tfvars")
    production = read("infra/terraform/parameters/production.tfvars")
    variables = read("infra/terraform/variables.tf")

    for name in (
        "api-5xx",
        "api-p95",
        "worker-task-failed",
        "cancel-ack",
        "redis-circuit-open",
        "cosmos-429",
        "session-oom",
        "session-capacity",
        "session-stop",
        "worker-ready-replicas",
    ):
        assert name in budgets
    assert "threshold = 50" in budgets
    assert "threshold = 75" in budgets
    assert "threshold = 90" in budgets
    assert "threshold = 100" in budgets
    assert 'thresholdType = "Forecasted"' in budgets

    assert "location" in demo and '"southeastasia"' in demo
    assert "profile" in demo and '"demo"' in demo
    assert "api_min_replicas                   = 0" in demo
    assert "api_max_replicas                   = 1" in demo
    assert "worker_min_replicas" not in demo
    assert "worker_max_replicas" not in demo
    assert "redis_sku" in demo and '"Balanced_B0"' in demo
    assert "location" in production and '"southeastasia"' in production
    assert "profile" in production and '"production"' in production
    assert "api_min_replicas                   = 3" in production
    assert "api_max_replicas                   = 10" in production
    assert "worker_min_replicas" not in production
    assert "worker_max_replicas" not in production
    assert "redis_sku" in production and '"Balanced_B10"' in production
    assert "zone_redundancy_enabled" in production and "true" in production
    assert 'variable "ready_sessions"' not in variables
    assert "ready_sessions" not in demo
    assert "ready_sessions" not in production
