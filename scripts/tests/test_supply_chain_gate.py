from __future__ import annotations

import os
import stat
import subprocess
import sys
import tomllib
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "scripts" / "supply_chain_policy.py"
GATE_SCRIPT = ROOT / "scripts" / "run-supply-chain-gate.sh"
SIGN_SCRIPT = ROOT / "scripts" / "sign-images.sh"
SBOM_SCRIPT = ROOT / "scripts" / "generate-sbom.sh"
VULNERABILITY_SCRIPT = ROOT / "scripts" / "scan-vulnerabilities.sh"


def load_module() -> Any:
    specification = spec_from_file_location("supply_chain_policy", MODULE_PATH)
    assert specification is not None
    assert specification.loader is not None
    module = module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


def test_supply_chain_scripts_bind_all_lockfiles_sboms_and_keyless_identity() -> None:
    gate = GATE_SCRIPT.read_text(encoding="utf-8")
    sbom = SBOM_SCRIPT.read_text(encoding="utf-8")
    vulnerabilities = VULNERABILITY_SCRIPT.read_text(encoding="utf-8")
    signing = SIGN_SCRIPT.read_text(encoding="utf-8")

    assert "eval " not in gate
    assert "--exclude-sbom uv-lock.cdx.json" in gate
    assert "--overrides docs/security/license-overrides.json" in gate
    assert 'syft "file:uv.lock"' in sbom
    assert "npm sbom --omit=dev --package-lock-only --sbom-format cyclonedx" in sbom
    assert "npm --prefix services/sandbox sbom --omit=dev --package-lock-only --sbom-format cyclonedx" in sbom
    assert 'grype "sbom:$sbom_file"' in vulnerabilities
    assert 'trivy image --format json --severity HIGH,CRITICAL "$image"' in vulnerabilities
    assert "COSIGN_CERTIFICATE_IDENTITY_REGEXP" in signing
    assert "COSIGN_CERTIFICATE_OIDC_ISSUER" in signing
    assert "--certificate-identity-regexp" in signing
    assert "--certificate-oidc-issuer" in signing


def test_workspace_constrains_cffi_to_mit_licensed_release() -> None:
    manifest = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert "cffi==2.0.0" in manifest["tool"]["uv"]["constraint-dependencies"]


def test_critical_vulnerability_without_waiver_fails() -> None:
    module = load_module()

    with pytest.raises(module.SupplyChainPolicyError, match="unwaived"):
        module.validate_vulnerabilities([{"id": "CVE-2026-0001", "severity": "Critical"}], [], today="2026-07-25")


def test_expired_waiver_fails_again() -> None:
    module = load_module()
    findings = [{"id": "CVE-2026-0001", "severity": "High"}]
    waivers = [{"id": "CVE-2026-0001", "ticket": "SEC-123", "expires": "2026-07-24"}]

    with pytest.raises(module.SupplyChainPolicyError, match="expired"):
        module.validate_vulnerabilities(findings, waivers, today="2026-07-25")


def test_reviewed_future_waiver_is_accepted() -> None:
    module = load_module()
    findings = [{"id": "CVE-2026-0001", "severity": "High"}]
    waivers = [{"id": "CVE-2026-0001", "ticket": "SEC-123", "expires": "2026-08-01"}]

    module.validate_vulnerabilities(findings, waivers, today="2026-07-25")


def test_waiver_requires_ticket_and_expiry() -> None:
    module = load_module()

    with pytest.raises(module.SupplyChainPolicyError, match="ticket"):
        module.validate_vulnerabilities(
            [{"id": "CVE-2026-0001", "severity": "High"}],
            [{"id": "CVE-2026-0001", "expires": "2026-08-01"}],
            today="2026-07-25",
        )


def test_digest_reference_rejects_image_tags() -> None:
    module = load_module()

    with pytest.raises(module.SupplyChainPolicyError, match="digest"):
        module.validate_digest_image_ref("example.azurecr.io/eda-api:latest", label="api")


def write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def run_script(path: Path, environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [str(path)],
        check=False,
        capture_output=True,
        text=True,
        cwd=ROOT,
        env=environment,
    )


def fake_step(path: Path, label: str, exit_code: int = 0) -> None:
    write_executable(
        path,
        f"#!/usr/bin/env bash\nset -euo pipefail\nprintf '%s\\n' '{label}' >> \"$ORDER_LOG\"\nexit {exit_code}\n",
    )


def base_environment(tmp_path: Path) -> dict[str, str]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    order_log = tmp_path / "order.log"

    for tool in ("jq", "python3"):
        write_executable(fake_bin / tool, f'#!/usr/bin/env bash\nexec /usr/bin/{tool} "$@"\n')

    generate = tmp_path / "generate-sbom.sh"
    verify = tmp_path / "verify-license.sh"
    scan_vulns = tmp_path / "scan-vulnerabilities.sh"
    scan_secrets = tmp_path / "scan-secrets.sh"
    sign = tmp_path / "sign-images.sh"
    for path, label in (
        (generate, "generate-sbom"),
        (verify, "verify-license"),
        (scan_vulns, "scan-vulnerabilities"),
        (scan_secrets, "scan-secrets"),
        (sign, "sign-images"),
    ):
        fake_step(path, label)

    environment = os.environ.copy()
    environment.update(
        {
            "PATH": f"{fake_bin}:{environment['PATH']}",
            "ORDER_LOG": str(order_log),
            "GENERATE_SBOM_CMD": str(generate),
            "VERIFY_LICENSE_CMD": str(verify),
            "SCAN_VULNERABILITIES_CMD": str(scan_vulns),
            "SCAN_SECRETS_CMD": str(scan_secrets),
            "SIGN_IMAGES_CMD": str(sign),
            "EDA_API_IMAGE": f"example.azurecr.io/eda-api@sha256:{'1' * 64}",
            "EDA_WORKER_IMAGE": f"example.azurecr.io/eda-worker@sha256:{'2' * 64}",
            "EDA_SANDBOX_IMAGE": f"example.azurecr.io/eda-sandbox@sha256:{'3' * 64}",
        }
    )
    return environment


def test_composed_gate_runs_scripts_in_order(tmp_path: Path) -> None:
    environment = base_environment(tmp_path)

    result = run_script(GATE_SCRIPT, environment)

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "order.log").read_text(encoding="utf-8").splitlines() == [
        "generate-sbom",
        "verify-license",
        "scan-vulnerabilities",
        "scan-secrets",
        "sign-images",
    ]


def test_composed_gate_fails_closed_on_first_failure(tmp_path: Path) -> None:
    environment = base_environment(tmp_path)
    fake_step(Path(environment["SCAN_VULNERABILITIES_CMD"]), "scan-vulnerabilities", exit_code=23)

    result = run_script(GATE_SCRIPT, environment)

    assert result.returncode == 23
    assert (tmp_path / "order.log").read_text(encoding="utf-8").splitlines() == [
        "generate-sbom",
        "verify-license",
        "scan-vulnerabilities",
    ]


def test_sign_script_rejects_tagged_images(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    write_executable(
        fake_bin / "cosign",
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'if [[ "$1" == version ]]; then\n'
        "  printf '%s\\n' '  GitVersion:    v3.1.2'\n"
        "  exit 0\n"
        "fi\n"
        "exit 0\n",
    )
    environment = os.environ.copy()
    environment.update(
        {
            "PATH": f"{fake_bin}:{environment['PATH']}",
            "EDA_API_IMAGE": "example.azurecr.io/eda-api:latest",
            "EDA_WORKER_IMAGE": f"example.azurecr.io/eda-worker@sha256:{'2' * 64}",
            "EDA_SANDBOX_IMAGE": f"example.azurecr.io/eda-sandbox@sha256:{'3' * 64}",
        }
    )

    result = run_script(SIGN_SCRIPT, environment)

    assert result.returncode == 1
    assert "digest" in result.stderr
