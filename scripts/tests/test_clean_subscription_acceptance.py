from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "run-clean-subscription-acceptance.sh"
SUBSCRIPTION = "00000000-0000-0000-0000-000000000000"
TENANT = "11111111-1111-1111-1111-111111111111"
PRINCIPAL = "11111111-1111-1111-1111-111111111111"
ACCEPTANCE_PRINCIPAL = "22222222-2222-2222-2222-222222222222"
TERRA_PROFILE = "gpt-5.6-terra-medium-v1"


def write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def fake_az(path: Path) -> None:
    write_executable(
        path,
        """#!/usr/bin/env bash
set -euo pipefail
printf 'az %s\n' "$*" >> "$FAKE_ORDER_LOG"
case "$1 $2" in
  "account set") exit 0 ;;
  "account show")
    if [[ "$*" == *"--query id"* ]]; then
      printf '%s\n' "$CURRENT_SUBSCRIPTION"
    elif [[ "$*" == *"--query tenantId"* ]]; then
      printf '%s\n' "$AZURE_TENANT_ID"
    else
      printf '{"id":"%s","tenantId":"%s"}\n' "$CURRENT_SUBSCRIPTION" "$AZURE_TENANT_ID"
    fi
    ;;
  "group exists")
    printf '%s\n' "${FAKE_GROUP_EXISTS:-false}"
    ;;
  "group delete") exit 0 ;;
  "graph query") printf '0\n' ;;
  "ad app")
    if [[ "$3" == "list" && "$*" == *"length("* ]]; then
      printf '0\n'
    fi
    exit 0
    ;;
  *) exit 0 ;;
esac
""",
    )


def fake_azd(path: Path) -> None:
    write_executable(
        path,
        """#!/usr/bin/env bash
set -euo pipefail
printf 'azd %s\n' "$*" >> "$FAKE_ORDER_LOG"
case "$1 $2" in
  "env new") mkdir -p "$FAKE_ROOT/.azure/$3" ;;
  "env select"|"env set") ;;
  "env remove") rm -rf "$FAKE_ROOT/.azure/$3" ;;
  *) exit 0 ;;
esac
""",
    )


def fake_terraform(path: Path) -> None:
    write_executable(
        path,
        """#!/usr/bin/env bash
set -euo pipefail
printf 'terraform %s\n' "$*" >> "$FAKE_ORDER_LOG"
if [[ "$*" == "version -json" ]]; then
  printf '%s\n' '{"terraform_version":"1.15.8"}'
fi
""",
    )


def fake_step(path: Path, label: str, *, fail_for: str | None = None) -> None:
    failure = ""
    if fail_for is not None:
        failure = f'if [[ "$EDA_RELEASE_IAC_PATH" == "{fail_for}" ]]; then exit 42; fi\n'
    write_executable(
        path,
        (
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            f'printf \'%s:%s:%s\\n\' \'{label}\' "$EDA_RELEASE_IAC_PATH" "$CURRENT_ENV" >> "$FAKE_ORDER_LOG"\n'
            f"{failure}"
        ),
    )


def base_environment(tmp_path: Path, *, gate_failure: str | None = None) -> dict[str, str]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    product_context = tmp_path / "product-context"
    product_context.mkdir(mode=0o700)
    product_context.chmod(0o700)
    order_log = tmp_path / "order.log"
    manifest = tmp_path / "release.json"

    fake_az(fake_bin / "az")
    fake_azd(fake_bin / "azd")
    fake_terraform(fake_bin / "terraform")
    deploy_bicep = tmp_path / "deploy-bicep"
    deploy_terraform = tmp_path / "deploy-terraform"
    gate = tmp_path / "gate"
    teardown_bicep = tmp_path / "teardown-bicep"
    teardown_terraform = tmp_path / "teardown-terraform"
    identity_cleanup = tmp_path / "identity-cleanup"
    zero_check = tmp_path / "zero-check"
    fake_step(deploy_bicep, "deploy-bicep")
    fake_step(deploy_terraform, "deploy-terraform")
    fake_step(gate, "gate", fail_for=gate_failure)
    fake_step(teardown_bicep, "teardown-bicep")
    fake_step(teardown_terraform, "teardown-terraform")
    fake_step(identity_cleanup, "identity-cleanup")
    fake_step(zero_check, "zero-check")

    environment = os.environ.copy()
    for name in (
        "EDA_ENTRA_APP_CLIENT_ID",
        "FABRIC_CLIENT_ID",
        "FABRIC_AZURE_CONFIG_DIR",
        "FABRIC_TENANT_ID",
        "FABRIC_PROVIDER",
    ):
        environment.pop(name, None)
    environment.update(
        {
            "PATH": f"{fake_bin}:{environment['PATH']}",
            "FAKE_ROOT": str(ROOT),
            "FAKE_ORDER_LOG": str(order_log),
            "FAKE_GROUP_EXISTS": "false",
            "PRODUCT_AZURE_CONFIG_DIR": str(product_context),
            "EDA_RELEASE_DISPOSABLE_CONFIRMED": "true",
            "EDA_RELEASE_RUN_ID": "unit42",
            "EDA_RELEASE_SUBSCRIPTION_IDS": SUBSCRIPTION,
            "EDA_RELEASE_MODEL_PROFILES": TERRA_PROFILE,
            "EDA_RELEASE_MANIFEST": str(manifest),
            "AZURE_TENANT_ID": TENANT,
            "AZURE_PRINCIPAL_ID": PRINCIPAL,
            "EDA_ACCEPTANCE_PRINCIPAL_ID": ACCEPTANCE_PRINCIPAL,
            "AZURE_MONTHLY_BUDGET_AMOUNT": "100",
            "AZURE_LOCATION": "southeastasia",
            "EDA_DEFENDER_CONFIRMED": "true",
            "EDA_SANDBOXES_PREVIEW_CONFIRMED": "true",
            "EDA_REDIS_SKU_CONFIRMED": "true",
            "FABRIC_ENABLED": "false",
            "DOCUMENTS_ENABLED": "false",
            "POWERBI_PROJECT_ENABLED": "false",
            "RELEASE_BICEP_DEPLOY_CMD": str(deploy_bicep),
            "RELEASE_TERRAFORM_DEPLOY_CMD": str(deploy_terraform),
            "RELEASE_GATE_CMD": str(gate),
            "RELEASE_BICEP_TEARDOWN_CMD": str(teardown_bicep),
            "RELEASE_TERRAFORM_TEARDOWN_CMD": str(teardown_terraform),
            "RELEASE_IDENTITY_CLEANUP_CMD": str(identity_cleanup),
            "RELEASE_ZERO_CHECK_CMD": str(zero_check),
        }
    )
    return environment


def run(environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [str(SCRIPT)],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


def step_lines(tmp_path: Path) -> list[str]:
    return [
        line
        for line in (tmp_path / "order.log").read_text(encoding="utf-8").splitlines()
        if line.startswith(
            (
                "deploy-",
                "gate:",
                "teardown-",
                "identity-cleanup:",
                "zero-check:",
            )
        )
    ]


def test_release_matrix_runs_bicep_then_terraform_and_verifies_teardown(tmp_path: Path) -> None:
    environment = base_environment(tmp_path)

    result = run(environment)

    assert result.returncode == 0, result.stderr
    assert step_lines(tmp_path) == [
        "deploy-bicep:bicep:rel-unit42-bicep",
        "gate:bicep:rel-unit42-bicep",
        "teardown-bicep:bicep:rel-unit42-bicep",
        "identity-cleanup:bicep:rel-unit42-bicep",
        "zero-check:bicep:rel-unit42-bicep",
        "deploy-terraform:terraform:rel-unit42-terraform",
        "gate:terraform:rel-unit42-terraform",
        "teardown-terraform:terraform:rel-unit42-terraform",
        "identity-cleanup:terraform:rel-unit42-terraform",
        "zero-check:terraform:rel-unit42-terraform",
    ]
    manifest_path = Path(environment["EDA_RELEASE_MANIFEST"])
    assert stat.S_IMODE(manifest_path.stat().st_mode) == 0o600
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["state"] == "passed"
    assert [cell["iac"] for cell in manifest["cells"]] == ["bicep", "terraform"]
    assert all(cell["teardown"] == "verified_zero" for cell in manifest["cells"])
    assert SUBSCRIPTION not in manifest_path.read_text(encoding="utf-8")


def test_failed_bicep_gate_still_tears_down_and_runs_terraform_cell(tmp_path: Path) -> None:
    environment = base_environment(tmp_path, gate_failure="bicep")

    result = run(environment)

    assert result.returncode != 0
    lines = step_lines(tmp_path)
    assert "teardown-bicep:bicep:rel-unit42-bicep" in lines
    assert "zero-check:bicep:rel-unit42-bicep" in lines
    assert "deploy-terraform:terraform:rel-unit42-terraform" in lines
    assert "teardown-terraform:terraform:rel-unit42-terraform" in lines
    assert not Path(environment["EDA_RELEASE_MANIFEST"]).exists()


def test_release_rejects_any_non_terra_profile_before_deployment(tmp_path: Path) -> None:
    environment = base_environment(tmp_path)
    environment["EDA_RELEASE_MODEL_PROFILES"] = "claude-opus-4-8-xhigh-v1"

    result = run(environment)

    assert result.returncode != 0
    assert "Terra only" in result.stderr
    assert not (tmp_path / "order.log").exists()


def test_existing_resource_group_is_never_adopted_or_deleted(tmp_path: Path) -> None:
    environment = base_environment(tmp_path)
    environment["FAKE_GROUP_EXISTS"] = "true"

    result = run(environment)

    assert result.returncode != 0
    assert "already exists" in result.stderr
    order = (tmp_path / "order.log").read_text(encoding="utf-8")
    assert "group delete" not in order
    assert "teardown-bicep" not in order


def test_default_commands_use_azd_up_only_for_bicep_and_explicit_terraform_destroy() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    terraform_deploy = source.split("deploy_terraform()", maxsplit=1)[1].split("selected_eval_results()", maxsplit=1)[0]

    assert 'azd up --environment "$CURRENT_ENV" --no-prompt' in source
    assert "azd up" not in terraform_deploy
    assert "azd provision" not in terraform_deploy
    assert 'terraform -chdir="$ROOT/infra/terraform" destroy' in source
    assert "azd down --environment" in source
    assert "product_az graph query" in source
    assert "EDA_RELEASE_BICEP_EVAL_RESULTS" in source
    assert "EDA_RELEASE_TERRAFORM_EVAL_RESULTS" in source
