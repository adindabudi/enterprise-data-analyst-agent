from __future__ import annotations

import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "configure-fabric-entra.sh"
TENANT_ID = "11111111-1111-1111-1111-111111111111"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o700)


def _environment(tmp_path: Path, *, topology: str | None) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    context = tmp_path / "azure"
    context.mkdir(mode=0o700)
    _write_executable(
        bin_dir / "az",
        """#!/usr/bin/env bash
set -euo pipefail
    printf '%s\n' "$*" >> "$FAKE_AZ_LOG"
case "${1:-} ${2:-} ${3:-}" in
  "account show "*) printf '%s\\n' "$FAKE_TENANT_ID" ;;
    "ad app list"*) cat <<'JSON'
[{"appId":"22222222-2222-2222-2222-222222222222",
    "id":"33333333-3333-3333-3333-333333333333",
    "signInAudience":"AzureADMyOrg","passwordCredentials":[]}]
JSON
        ;;
    "ad app show"*)
        if [[ -n "${FAKE_APP_JSON:-}" ]]; then
            printf '%s\n' "$FAKE_APP_JSON"
            exit 0
        fi
        cat <<'JSON'
{"appId":"22222222-2222-2222-2222-222222222222",
 "id":"33333333-3333-3333-3333-333333333333",
 "signInAudience":"AzureADMyOrg","passwordCredentials":[],"keyCredentials":[]}
JSON
                ;;
    "ad sp show"*)
        if [[ "$*" != *"00000009-0000-0000-c000-000000000000"* ]]; then
            exit 3
        fi
        cat <<'JSON'
{"oauth2PermissionScopes":[
    {"value":"Item.Read.All","id":"33333333-3333-3333-3333-333333333333","isEnabled":true},
    {"value":"Item.ReadWrite.All","id":"44444444-4444-4444-4444-444444444444","isEnabled":true},
    {"value":"Item.Execute.All","id":"55555555-5555-5555-5555-555555555555","isEnabled":true}
]}
JSON
        ;;
  "ad sp create"*) exit 0 ;;
  "ad app update"*) exit 0 ;;
    "ad app credential"*) exit 0 ;;
  *) printf 'unexpected az command: %s\\n' "$*" >&2; exit 90 ;;
esac
""",
    )
    _write_executable(
        bin_dir / "azd",
        """#!/usr/bin/env bash
set -euo pipefail
[[ "${1:-} ${2:-}" == "env set" ]]
""",
    )
    environment = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "FABRIC_ENABLED": "true",
        "FABRIC_PROVIDER": "ontology",
        "FABRIC_AZURE_CONFIG_DIR": str(context),
        "PRODUCT_AZURE_CONFIG_DIR": str(context),
        "FABRIC_TENANT_ID": TENANT_ID,
        "AZURE_TENANT_ID": TENANT_ID,
        "AZURE_ENV_NAME": "eda-smoke",
        "FAKE_TENANT_ID": TENANT_ID,
        "FAKE_AZ_LOG": str(tmp_path / "az.log"),
    }
    if topology is not None:
        environment["FABRIC_TOPOLOGY"] = topology
    else:
        environment.pop("FABRIC_TOPOLOGY", None)
    return environment


def _write_public_certificate(path: Path) -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "eda-fabric-test")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .sign(private_key, hashes.SHA256())
    )
    path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))


def test_same_tenant_smoke_allows_explicit_same_tenant_bootstrap(tmp_path: Path) -> None:
    environment = _environment(tmp_path, topology="same_tenant_smoke")
    result = subprocess.run(  # noqa: S603 - executes the trusted repository script.
        [str(SCRIPT), "bootstrap"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Fabric OAuth bootstrap complete" in result.stdout
    assert "ad sp create --id 22222222-2222-2222-2222-222222222222" in Path(environment["FAKE_AZ_LOG"]).read_text(
        encoding="utf-8"
    )


def test_same_tenant_smoke_reuses_existing_broader_grant_without_graph_write(tmp_path: Path) -> None:
    environment = _environment(tmp_path, topology="same_tenant_smoke")
    environment["FABRIC_CLIENT_ID"] = "22222222-2222-2222-2222-222222222222"
    environment["FAKE_APP_JSON"] = """{
        "appId":"22222222-2222-2222-2222-222222222222",
        "id":"33333333-3333-3333-3333-333333333333",
        "signInAudience":"AzureADMyOrg",
        "passwordCredentials":[],
        "keyCredentials":[],
        "requiredResourceAccess":[{
            "resourceAppId":"00000009-0000-0000-c000-000000000000",
            "resourceAccess":[
                {"id":"44444444-4444-4444-4444-444444444444","type":"Scope"},
                {"id":"55555555-5555-5555-5555-555555555555","type":"Scope"}
            ]
        }]
    }"""

    result = subprocess.run(  # noqa: S603 - executes the trusted repository script.
        [str(SCRIPT), "bootstrap"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "same-tenant smoke retains its existing non-production Fabric grant" in result.stdout
    assert "ad app update" not in Path(environment["FAKE_AZ_LOG"]).read_text(encoding="utf-8")


def test_cross_tenant_default_still_rejects_equal_tenants(tmp_path: Path) -> None:
    result = subprocess.run(  # noqa: S603 - executes the trusted repository script.
        [str(SCRIPT), "bootstrap"],
        cwd=ROOT,
        env=_environment(tmp_path, topology=None),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "distinct tenant IDs" in result.stderr


def test_finalize_accepts_public_certificate_without_vault_access(tmp_path: Path) -> None:
    certificate_path = tmp_path / "fabric-oauth-signing.pem"
    _write_public_certificate(certificate_path)
    environment = _environment(tmp_path, topology="same_tenant_smoke")
    environment.update(
        {
            "FABRIC_CLIENT_ID": "22222222-2222-2222-2222-222222222222",
            "FABRIC_KEY_VAULT_URL": "https://private-vault.vault.azure.net/",
            "FABRIC_PUBLIC_CERTIFICATE_FILE": str(certificate_path),
            "API_URL": "https://eda.example.test",
        }
    )

    result = subprocess.run(  # noqa: S603 - executes the trusted repository script.
        [str(SCRIPT), "finalize"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Fabric OAuth finalize complete" in result.stdout
    assert "Administrator action required" not in result.stdout
    assert "otherwise request administrator consent" in result.stdout


def test_finalize_reuses_one_valid_matching_public_credential(tmp_path: Path) -> None:
    environment = _environment(tmp_path, topology="same_tenant_smoke")
    environment.update(
        {
            "FABRIC_CLIENT_ID": "22222222-2222-2222-2222-222222222222",
            "FABRIC_KEY_VAULT_URL": "https://private-vault.vault.azure.net/",
            "API_URL": "https://eda.example.test",
            "FAKE_APP_JSON": """{
                "appId":"22222222-2222-2222-2222-222222222222",
                "id":"33333333-3333-3333-3333-333333333333",
                "signInAudience":"AzureADMyOrg",
                "passwordCredentials":[],
                "keyCredentials":[{
                    "displayName":"fabric-oauth-signing",
                    "usage":"Verify",
                    "endDateTime":"2099-01-01T00:00:00Z"
                }],
                "web":{"redirectUris":["https://eda.example.test/api/fabric/auth/callback"]}
            }""",
        }
    )

    result = subprocess.run(  # noqa: S603 - executes the trusted repository script.
        [str(SCRIPT), "finalize"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Fabric OAuth finalize already complete" in result.stdout
    assert "keyvault" not in Path(environment["FAKE_AZ_LOG"]).read_text(encoding="utf-8")
