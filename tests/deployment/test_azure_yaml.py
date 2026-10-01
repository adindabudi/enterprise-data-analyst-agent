from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_azure_yaml_deploys_only_the_api_with_the_analysis_runtime_in_safe_hook_order() -> None:
    manifest = yaml.safe_load((ROOT / "azure.yaml").read_text(encoding="utf-8"))

    assert manifest["requiredVersions"] == {"azd": ">=1.31.1"}
    assert set(manifest["services"]) == {"api"}
    api = manifest["services"]["api"]
    assert api["docker"]["remoteBuild"] is True
    # The API image carries the analyst runtime, with skill bundles copied from the pinned worker image.
    assert api["docker"]["buildArgs"] == ["EDA_WORKER_IMAGE=${EDA_WORKER_IMAGE}"]
    manifest_text = (ROOT / "azure.yaml").read_text(encoding="utf-8")
    assert "azure.ai.agent" not in manifest_text
    assert "={}}" not in manifest_text
    assert manifest["infra"] == {
        "provider": "bicep",
        "path": "infra/bicep",
        "module": "main",
    }
    hooks = manifest["hooks"]
    assert hooks["preprovision"]["run"] == (
        "./scripts/doctor.sh && ./scripts/doctor-documents.sh --phase prebuild "
        "&& ./scripts/ensure-entra-app.sh && ./scripts/preflight-model.sh "
        "&& ./scripts/configure-fabric-entra.sh bootstrap && ./scripts/run-fabric-provider-hook.sh preprovision"
    )
    assert hooks["postprovision"]["run"] == (
        "uv run python scripts/sync-bicep-outputs.py && "
        "./scripts/configure-fabric-entra.sh finalize "
        "&& ./scripts/build-worker-image.sh && ./scripts/build-sandbox-image.sh && ./scripts/deploy-sandbox-group.sh "
        "&& ./scripts/configure-entra-federation.sh"
    )
    assert hooks["postdeploy"]["run"] == (
        "./scripts/doctor-documents.sh --phase postdeploy --output .artifacts/document-image-contract.json "
        "&& ./scripts/publish-document-contract-job.sh && ./scripts/pin-application-images.sh "
        "&& ./scripts/deploy-smoke.sh"
    )


def test_doctor_is_noninteractive_and_validates_prerequisites_without_static_azure_access() -> None:
    source = (ROOT / "scripts/doctor.sh").read_text(encoding="utf-8")

    assert "set -eu" in source
    assert "EDA_DOCTOR_NONINTERACTIVE" in source
    assert "AZURE_SUBSCRIPTION_ID" in source
    assert "AZURE_TENANT_ID" in source
    assert "AZURE_LOCATION" in source
    assert "MONTHLY_BUDGET_AMOUNT" in source
    assert "az account show" in source
    for command in ("az", "azd", "docker", "jq", "uv", "node", "npm"):
        assert f'require_command "{command}"' in source
    assert "password" not in source.lower()
    assert "token" not in source.lower()


def test_budget_alerts_are_parameterized_and_do_not_query_sensitive_content() -> None:
    source = (ROOT / "infra/bicep/modules/budgets-alerts.bicep").read_text(encoding="utf-8")
    main = (ROOT / "infra/bicep/main.bicep").read_text(encoding="utf-8")

    for parameter in ("monthlyBudgetAmount", "budgetActionGroupName", "budgetAlertEmail"):
        assert f"param {parameter}" in main
    assert "param monitoringAlertsEnabled bool = false" in main
    assert "module budgetsAlerts 'modules/budgets-alerts.bicep' = if (monitoringAlertsEnabled)" in main
    for parameter in ("monthlyBudgetAmount", "actionGroupName", "alertEmail"):
        assert f"param {parameter}" in source
    for threshold in ("50", "75", "90", "100"):
        assert f"threshold: {threshold}" in source
    assert "forecasted" in source

    for alert in (
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
        assert alert in source
    for forbidden_term in ("prompt", "file", "blob", "message", "content", "input"):
        assert forbidden_term not in source.lower()


def test_main_uses_the_explicit_azd_resource_group_name() -> None:
    main = (ROOT / "infra/bicep/main.bicep").read_text(encoding="utf-8")
    parameters = (ROOT / "infra/bicep/main.parameters.json").read_text(encoding="utf-8")

    assert "param resourceGroupName string" in main
    assert "name: resourceGroupName" in main
    assert '"${AZURE_RESOURCE_GROUP}"' in parameters


def test_fresh_provision_uses_placeholders_until_postprovision_builds_images() -> None:
    parameters = (ROOT / "infra/bicep/main.parameters.json").read_text(encoding="utf-8")

    assert "${EDA_API_IMAGE=example.azurecr.io/eda-api@sha256:" in parameters
    assert "${EDA_WORKER_IMAGE=example.azurecr.io/eda-worker@sha256:" in parameters


def test_remote_builds_apply_source_revision_through_supported_build_arguments() -> None:
    script = (ROOT / "scripts/build-sandbox-image.sh").read_text(encoding="utf-8")
    assert '--build-arg "SOURCE_REVISION=${source_revision}"' in script
    assert "--label" not in script

    for dockerfile in ("apps/api/Dockerfile", "services/worker/Dockerfile", "services/sandbox/Dockerfile"):
        source = (ROOT / dockerfile).read_text(encoding="utf-8")
        assert "ARG SOURCE_REVISION" in source
        assert "LABEL org.opencontainers.image.revision=$SOURCE_REVISION" in source
        assert "--mount=type=cache" not in source

    for dockerfile in ("apps/api/Dockerfile", "services/worker/Dockerfile"):
        source = (ROOT / dockerfile).read_text(encoding="utf-8")
        assert "COPY packages/fabric-auth packages/fabric-auth" in source
        assert "uv build --wheel --out-dir /wheels packages/fabric-auth" in source

    api = (ROOT / "apps/api/Dockerfile").read_text(encoding="utf-8")
    assert "EDA_FRONTEND_DIST=/app/static" in api

    worker = (ROOT / "services/worker/Dockerfile").read_text(encoding="utf-8")
    assert "COPY .artifacts/model-contract.json /app/config/model-contract.json" in worker
    assert "COPY .artifacts/tokenizer-calibration.json /app/config/tokenizer-calibration.json" in worker

    sandbox = (ROOT / "services/sandbox/Dockerfile").read_text(encoding="utf-8")
    assert "ARG DEBIAN_SNAPSHOT=20260726T000000Z" in sandbox
    assert "apt-get upgrade -y" in sandbox


def test_operations_documents_cover_slos_cost_controls_and_recovery() -> None:
    required_documents = {
        "docs/operations/slo.md": ("p95", "500ms", "first progress", "1s", "first model content", "10s"),
        "docs/operations/cost-controls.md": ("demo", "production", "ready sessions", "retention", "commitment"),
        "docs/runbooks/redis-outage.md": ("degraded", "circuit", "recovery"),
        "docs/runbooks/storage-restore.md": ("PITR", "soft delete", "Blob"),
        "docs/runbooks/rollback.md": ("ACA revision", "model", "image", "worker drain"),
    }

    for relative_path, expected_terms in required_documents.items():
        content = (ROOT / relative_path).read_text(encoding="utf-8").lower()
        for term in expected_terms:
            assert term.lower() in content
