from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_azure_yaml_orchestrates_api_and_hosted_agent_in_safe_hook_order() -> None:
    manifest = yaml.safe_load((ROOT / "azure.yaml").read_text(encoding="utf-8"))

    assert manifest["requiredVersions"] == {
        "azd": ">=1.31.1",
        "extensions": {"azure.ai.agents": ">=1.0.0-beta.11"},
    }
    assert set(manifest["services"]) == {"api", "ai-project", "long-job"}
    assert manifest["services"]["api"]["docker"]["remoteBuild"] is True
    assert manifest["services"]["ai-project"] == {
        "host": "azure.ai.project",
        "endpoint": "${FOUNDRY_PROJECT_ENDPOINT}",
    }
    hosted = manifest["services"]["long-job"]
    assert hosted["host"] == "azure.ai.agent"
    assert hosted["uses"] == ["ai-project"]
    assert hosted["kind"] == "hosted"
    assert hosted["protocols"] == [{"protocol": "responses", "version": "2.0.0"}]
    assert hosted["container"] == {"resources": {"cpu": "0.5", "memory": "1Gi"}}
    assert hosted["sessionConfiguration"] == {"idleTimeoutSeconds": 300}
    assert hosted["image"] == "${EDA_WORKER_IMAGE}"
    assert "docker" not in hosted
    assert hosted["env"]["EDA_APP_ENV"] == "production"
    assert hosted["env"]["ENABLE_CONSOLE_EXPORTERS"] == "false"
    assert hosted["env"]["EDA_FOUNDRY_MODEL_DEPLOYMENT"] == "${AZURE_AI_MODEL_DEPLOYMENT_NAME}"
    assert hosted["env"]["DOCUMENTS_ENABLED"] == "${DOCUMENTS_ENABLED=false}"
    assert hosted["env"]["FABRIC_ENABLED"] == "${FABRIC_ENABLED=false}"
    assert hosted["env"]["FABRIC_PROVIDER"] == "${FABRIC_PROVIDER=}"
    assert hosted["env"]["FABRIC_KEY_VAULT_URL"] == "${FABRIC_KEY_VAULT_URL=}"
    assert hosted["env"]["FABRIC_SIGNING_CERTIFICATE_NAME"] == (
        "${FABRIC_SIGNING_CERTIFICATE_NAME=fabric-oauth-signing}"
    )
    assert hosted["env"]["FABRIC_CACHE_WRAP_KEY_NAME"] == "${FABRIC_CACHE_WRAP_KEY_NAME=fabric-cache-wrap}"
    assert hosted["env"]["FABRIC_SEMANTIC_MODELS_JSON"] == "${FABRIC_SEMANTIC_MODELS_JSON=}"
    assert hosted["env"]["FABRIC_ONTOLOGIES_JSON"] == "${FABRIC_ONTOLOGIES_JSON=}"
    assert "={}}" not in (ROOT / "azure.yaml").read_text(encoding="utf-8")
    assert "FABRIC_RUNTIME_ENABLED" not in hosted["env"]
    assert "FOUNDRY_PROJECT_ENDPOINT" not in hosted["env"]
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
        "./scripts/configure-fabric-entra.sh finalize && ./scripts/run-fabric-provider-hook.sh postprovision "
        "&& ./scripts/build-worker-image.sh && ./scripts/build-sandbox-image.sh && ./scripts/deploy-sandbox-group.sh "
        "&& ./scripts/configure-entra-federation.sh"
    )
    assert hooks["postdeploy"]["run"] == (
        "uv run python scripts/bind-hosted-agent-rbac.py "
        "&& ./scripts/doctor-documents.sh --phase postdeploy --output .artifacts/document-image-contract.json "
        "&& ./scripts/publish-document-contract-job.sh && ./scripts/pin-application-images.sh "
        "&& ./scripts/run-fabric-provider-hook.sh postdeploy "
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
