from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/deploy-terraform-environment.sh"
SANDBOX_DIGEST = f"example.azurecr.io/eda-sandbox@sha256:{'1' * 64}"
WORKER_DIGEST = f"example.azurecr.io/eda-worker@sha256:{'2' * 64}"
API_DIGEST = f"example.azurecr.io/eda-api@sha256:{'3' * 64}"
WORKER_IMAGE_PREFIX = "worker_image=example.azurecr.io/eda-worker@sha256:"


def write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def fake_step(path: Path, label: str, body: str = "") -> None:
    write_executable(
        path,
        (f"#!/usr/bin/env bash\nset -euo pipefail\nprintf '%s\\n' '{label}' >> \"$FAKE_ORDER_LOG\"\n{body}\n"),
    )


def fake_azd(path: Path) -> None:
    write_executable(
        path,
        """#!/usr/bin/env bash
set -euo pipefail
printf 'azd %s\n' "$*" >> "$FAKE_ORDER_LOG"
ENV_FILE="$FAKE_AZD_ENV_FILE"
touch "$ENV_FILE"
case "$1 $2" in
  "env get-values")
  cat "$ENV_FILE"
  ;;
  "env set")
  key="$3"
  value="$4"
  grep -v "^${key}=" "$ENV_FILE" > "$ENV_FILE.tmp" || true
  mv "$ENV_FILE.tmp" "$ENV_FILE"
  printf '%s="%s"\n' "$key" "$value" >> "$ENV_FILE"
  ;;
  "env new"|"env select")
  ;;
    "deploy --all")
    printf '%s="%s"\n' EDA_API_IMAGE "$FAKE_API_IMAGE" >> "$ENV_FILE"
  ;;
  *)
  if [[ "$*" == *"provision"* || "$*" == *" up"* ]]; then
    printf 'unexpected azd command: %s\n' "$*" >&2
    exit 97
  fi
  ;;
esac
""",
    )


def fake_terraform(path: Path, output_json: str) -> None:
    write_executable(
        path,
        f"""#!/usr/bin/env bash
set -euo pipefail
printf 'terraform %s\n' "$*" >> "$FAKE_ORDER_LOG"
if [[ "$*" == *"init -backend=false"* ]]; then
  exit 0
fi
if [[ "$*" == "version -json" ]]; then
    printf '%s\n' '{{"terraform_version":"1.15.8"}}'
  exit 0
fi
if [[ "$*" == *"output"* && "$*" == *"-json"* ]]; then
  cat <<'JSON'
{output_json}
JSON
  exit 0
fi
if [[ "$*" == *"apply"* ]]; then
  if [[ "$*" == *"deploy_image_dependent_resources=false"* && "$*" == *"sandbox_image="* ]]; then
  echo foundation-apply-must-not-require-sandbox >&2
  exit 91
  fi
  if [[ "$*" == *"deploy_image_dependent_resources=true"* && "$*" != *"sandbox_image={SANDBOX_DIGEST}"* ]]; then
  echo missing-sandbox-digest >&2
  exit 92
  fi
  if [[ "$*" == *"deploy_image_dependent_resources=true"* && "$*" != *"{WORKER_IMAGE_PREFIX}"* ]]; then
  echo missing-worker-digest >&2
  exit 93
  fi
  exit 0
fi
exit 0
""",
    )


def run(environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [str(SCRIPT)],
        check=False,
        capture_output=True,
        text=True,
        cwd=ROOT,
        env=environment,
    )


def base_environment(tmp_path: Path, output_json: str) -> dict[str, str]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    order_log = tmp_path / "order.log"
    env_file = tmp_path / "azd.env"
    env_file.write_text(
        (
            f'AZURE_ENV_NAME="tf-demo"\nAZURE_LOCATION="southeastasia"\n'
            f'EDA_SANDBOX_IMAGE="{SANDBOX_DIGEST}"\n'
            'ENTRA_CLIENT_ID="33333333-3333-3333-3333-333333333333"\n'
            'FABRIC_CLIENT_ID="44444444-4444-4444-4444-444444444444"\n'
        ),
        encoding="utf-8",
    )

    fake_azd(fake_bin / "azd")
    fake_terraform(fake_bin / "terraform", output_json)
    write_executable(fake_bin / "az", "#!/usr/bin/env bash\nexit 0\n")
    write_executable(fake_bin / "jq", '#!/usr/bin/env bash\nexec /usr/bin/jq "$@"\n')
    write_executable(fake_bin / "git", "#!/usr/bin/env bash\nexit 0\n")

    for name in (
        "ensure-entra-app.sh",
        "preflight-model.sh",
        "configure-entra-federation.sh",
        "doctor-fabric.sh",
        "doctor-fabric-ontology.sh",
        "configure-fabric-entra.sh",
        "run-fabric-provider-hook.sh",
        "run-document-acceptance.sh",
    ):
        fake_step(tmp_path / name, name)

    fake_step(
        tmp_path / "build-worker-image.sh",
        "build-worker-image.sh",
        f"printf '%s=\"%s\"\\n' EDA_WORKER_IMAGE '{WORKER_DIGEST}' >> \"$FAKE_AZD_ENV_FILE\"\n"
        f"printf '%s=\"%s\"\\n' EDA_WORKER_IMAGE_DIGEST 'sha256:{'2' * 64}' >> \"$FAKE_AZD_ENV_FILE\"",
    )
    fake_step(
        tmp_path / "build-sandbox-image.sh",
        "build-sandbox-image.sh",
        f"printf '%s=\"%s\"\\n' EDA_SANDBOX_IMAGE '{SANDBOX_DIGEST}' >> \"$FAKE_AZD_ENV_FILE\"",
    )
    fake_step(
        tmp_path / "create-sandbox-disk-image.sh", "create-sandbox-disk-image.sh", "printf 'disk_image_12345678\\n'"
    )

    environment = os.environ.copy()
    environment.update(
        {
            "PATH": f"{fake_bin}:{environment['PATH']}",
            "FAKE_ORDER_LOG": str(order_log),
            "FAKE_AZD_ENV_FILE": str(env_file),
            "FAKE_API_IMAGE": API_DIGEST,
            "TF_VAR_FILE": str(ROOT / "infra/terraform/parameters/demo.tfvars"),
            "TF_AZD_ENV_NAME": "tf-demo",
            "AZURE_ENV_NAME": "tf-demo",
            "AZURE_RESOURCE_GROUP": "rg-eda-tf-clean-sea",
            "AZURE_SUBSCRIPTION_ID": "00000000-0000-0000-0000-000000000000",
            "AZURE_TENANT_ID": "11111111-1111-1111-1111-111111111111",
            "AZURE_LOCATION": "southeastasia",
            "AZURE_PRINCIPAL_ID": "55555555-5555-5555-5555-555555555555",
            "EDA_ACCEPTANCE_PRINCIPAL_ID": "66666666-6666-6666-6666-666666666666",
            "EDA_MODEL_PROFILE": "gpt-5.6-terra-medium-v1",
            "FABRIC_TENANT_ID": "77777777-7777-7777-7777-777777777777",
            "FABRIC_CLIENT_ID": "44444444-4444-4444-4444-444444444444",
            "FABRIC_ENABLED": "false",
            "DOCUMENTS_ENABLED": "false",
            "POWERBI_PROJECT_ENABLED": "false",
            "ENSURE_ENTRA_APP_CMD": str(tmp_path / "ensure-entra-app.sh"),
            "PREFLIGHT_MODEL_CMD": str(tmp_path / "preflight-model.sh"),
            "CONFIGURE_ENTRA_FEDERATION_CMD": str(tmp_path / "configure-entra-federation.sh"),
            "BUILD_WORKER_IMAGE_CMD": str(tmp_path / "build-worker-image.sh"),
            "BUILD_SANDBOX_IMAGE_CMD": str(tmp_path / "build-sandbox-image.sh"),
            "CREATE_SANDBOX_DISK_IMAGE_CMD": str(tmp_path / "create-sandbox-disk-image.sh"),
            "DOCTOR_FABRIC_CMD": str(tmp_path / "doctor-fabric.sh"),
            "DOCTOR_FABRIC_ONTOLOGY_CMD": str(tmp_path / "doctor-fabric-ontology.sh"),
            "CONFIGURE_FABRIC_ENTRA_CMD": str(tmp_path / "configure-fabric-entra.sh"),
            "RUN_FABRIC_PROVIDER_HOOK_CMD": str(tmp_path / "run-fabric-provider-hook.sh"),
            "RUN_DOCUMENT_ACCEPTANCE_CMD": str(tmp_path / "run-document-acceptance.sh"),
        }
    )
    return environment


def standard_output_payload(*, fabric: bool = False) -> str:
    payload = {
        "resource_group_name": {"value": "rg-eda-dev-sea"},
        "deployment_id": {"value": "deployment-20260727"},
        "api_url": {"value": "https://api.example.test"},
        "profile": {"value": "demo"},
        "model_profile": {"value": "gpt-5.6-terra-medium-v1"},
        "model_deployment_name": {"value": "gpt-5.6-terra"},
        "project_endpoint": {"value": "https://foundry/api/projects/eda-project"},
        "container_registry_id": {
            "value": "/subscriptions/s/resourceGroups/r/providers/Microsoft.ContainerRegistry/registries/example"
        },
        "container_registry_login_server": {"value": "example.azurecr.io"},
        "cosmos_account_id": {
            "value": "/subscriptions/s/resourceGroups/r/providers/Microsoft.DocumentDB/databaseAccounts/cosmos"
        },
        "cosmos_endpoint": {"value": "https://cosmos.documents.azure.com:443/"},
        "storage_account_id": {
            "value": "/subscriptions/s/resourceGroups/r/providers/Microsoft.Storage/storageAccounts/st"
        },
        "storage_blob_endpoint": {"value": "https://st.blob.core.windows.net/"},
        "redis_cluster_id": {"value": "/subscriptions/s/resourceGroups/r/providers/Microsoft.Cache/redisEnterprise/re"},
        "redis_hostname": {"value": "redis.southeastasia.redis.azure.net"},
        "redis_port": {"value": 10000},
        "web_identity_id": {
            "value": "/subscriptions/s/resourceGroups/r/providers/Microsoft.ManagedIdentity/userAssignedIdentities/web"
        },
        "web_identity_client_id": {"value": "web-client"},
        "web_identity_principal_id": {"value": "web-principal"},
        "worker_identity_id": {
            "value": "/subscriptions/s/resourceGroups/r/providers/Microsoft.ManagedIdentity/userAssignedIdentities/worker"
        },
        "worker_identity_client_id": {"value": "worker-client"},
        "worker_identity_principal_id": {"value": "worker-principal"},
        "session_init_identity_id": {
            "value": "/subscriptions/s/resourceGroups/r/providers/Microsoft.ManagedIdentity/userAssignedIdentities/session"
        },
        "session_init_identity_client_id": {"value": "session-client"},
        "session_init_identity_principal_id": {"value": "session-principal"},
        "virtual_network_id": {
            "value": "/subscriptions/s/resourceGroups/r/providers/Microsoft.Network/virtualNetworks/vnet"
        },
        "aca_subnet_id": {
            "value": "/subscriptions/s/resourceGroups/r/providers/Microsoft.Network/virtualNetworks/vnet/subnets/aca"
        },
        "sandbox_subnet_id": {
            "value": "/subscriptions/s/resourceGroups/r/providers/Microsoft.Network/virtualNetworks/vnet/subnets/sandboxes"
        },
        "foundry_agent_subnet_id": {
            "value": "/subscriptions/s/resourceGroups/r/providers/Microsoft.Network/virtualNetworks/vnet/subnets/foundry-agents"
        },
        "private_endpoints_subnet_id": {
            "value": (
                "/subscriptions/s/resourceGroups/r/providers/Microsoft.Network/"
                "virtualNetworks/vnet/subnets/private-endpoints"
            )
        },
        "log_analytics_workspace_id": {"value": "workspace-id"},
        "app_insights_id": {"value": "/subscriptions/s/resourceGroups/r/providers/Microsoft.Insights/components/appi"},
        "container_apps_environment_id": {
            "value": "/subscriptions/s/resourceGroups/r/providers/Microsoft.App/managedEnvironments/cae"
        },
        "api_app_id": {"value": "/subscriptions/s/resourceGroups/r/providers/Microsoft.App/containerApps/api"},
        "cleanup_job_id": {"value": "/subscriptions/s/resourceGroups/r/providers/Microsoft.App/jobs/cleanup"},
        "fabric_acceptance_job_id": {
            "value": "/subscriptions/s/resourceGroups/r/providers/Microsoft.App/jobs/fabric" if fabric else None
        },
        "fabric_vault_url": {"value": "https://vault.vault.azure.net/" if fabric else None},
        "sandbox_group_id": {
            "value": "/subscriptions/s/resourceGroups/r/providers/Microsoft.App/sandboxGroups/sbg-eda-tf-demo"
        },
        "budget_action_group_id": {"value": None},
    }
    return json.dumps(payload)


def test_script_never_calls_azd_up_or_provision_and_deploys_all_services_once() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "azd deploy --all" in source
    assert "azd deploy api" not in source
    assert "azd deploy worker" not in source
    assert "azd provision" not in source
    assert "azd up" not in source


def test_bridge_remaps_final_outputs_for_the_api_and_sandbox() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert source.count("map_terraform_outputs_to_azd") >= 3
    assert "durable_task_endpoint" not in source
    assert "worker_app_id" not in source
    assert "session_pool_management_endpoint" not in source
    assert "EDA_SANDBOX_DISK_IMAGE_ID" in source
    assert "AZURE_AI_MODEL_DEPLOYMENT_NAME" in source
    assert "FOUNDRY_PROJECT_ENDPOINT" in source
    assert "azd env set API_URL" in source
    assert "terraform_version" in source and '"1.15.8"' in source
    assert 'chmod 600 "$destination"' in source
    assert "resource_group_name: $resourceGroupName" in source
    assert 'terraform_common_args=(-var-file="$TF_VAR_FILE" -var-file="$runtime_var_file")' in source
    assert '-state="$TF_STATE_PATH"' in source
    assert "export TF_DATA_DIR" in source
    assert 'chmod 700 "$(dirname "$TF_STATE_PATH")" "$TF_DATA_DIR"' in source


def test_bridge_runs_foundation_then_image_dependent_apply_in_order(tmp_path: Path) -> None:
    environment = base_environment(tmp_path, standard_output_payload())

    result = run(environment)

    assert result.returncode == 0, result.stderr
    order = (tmp_path / "order.log").read_text(encoding="utf-8").splitlines()
    foundation_apply = next(
        index
        for index, line in enumerate(order)
        if "terraform -chdir=" in line and "deploy_image_dependent_resources=false" in line
    )
    final_apply = next(
        index
        for index, line in enumerate(order)
        if "terraform -chdir=" in line and "deploy_image_dependent_resources=true" in line
    )
    assert order.index("ensure-entra-app.sh") < order.index("preflight-model.sh")
    assert order.index("preflight-model.sh") < foundation_apply
    assert order.index("build-worker-image.sh") < final_apply
    assert order.index("build-sandbox-image.sh") < final_apply
    assert order.index("create-sandbox-disk-image.sh") > final_apply
    assert order.index("create-sandbox-disk-image.sh") < order.index("azd deploy --all")
    values = (tmp_path / "azd.env").read_text(encoding="utf-8")
    assert 'EDA_SANDBOX_DISK_IMAGE_ID="disk_image_12345678"' in values
    assert 'EDA_SANDBOX_GROUP="sbg-eda-tf-demo"' in values


def test_bridge_rejects_unknown_or_secret_outputs(tmp_path: Path) -> None:
    output_json = json.dumps(
        {
            "resource_group_name": {"value": "rg-eda-dev-sea"},
            "profile": {"value": "demo"},
            "API_KEY": {"value": "should-not-pass"},
        }
    )
    environment = base_environment(tmp_path, output_json)

    result = run(environment)

    assert result.returncode != 0
    assert "unknown Terraform output" in result.stderr or "secret-looking Terraform output" in result.stderr


def test_bridge_rejects_missing_required_output(tmp_path: Path) -> None:
    payload = json.loads(standard_output_payload())
    payload["deployment_id"]["value"] = None
    environment = base_environment(tmp_path, json.dumps(payload))

    result = run(environment)

    assert result.returncode != 0
    assert "required Terraform output" in result.stderr


def test_bridge_requires_selected_fabric_bootstrap_and_finalize(tmp_path: Path) -> None:
    environment = base_environment(tmp_path, standard_output_payload(fabric=True))
    environment["FABRIC_ENABLED"] = "true"
    environment["FABRIC_PROVIDER"] = "ontology"

    result = run(environment)

    assert result.returncode == 0, result.stderr
    order = (tmp_path / "order.log").read_text(encoding="utf-8")
    assert "configure-fabric-entra.sh" in order
    assert "doctor-fabric-ontology.sh" in order
    assert order.rindex("configure-fabric-entra.sh") < order.index("azd deploy --all")
