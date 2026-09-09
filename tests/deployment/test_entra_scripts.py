from __future__ import annotations

import json
import os
import runpy
import subprocess
from email.message import Message
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import OpenerDirector

ROOT = Path(__file__).resolve().parents[2]
APP_OBJECT_ID = "11111111-1111-1111-1111-111111111111"
CLIENT_ID = "22222222-2222-2222-2222-222222222222"
TENANT_ID = "33333333-3333-3333-3333-333333333333"
WEB_PRINCIPAL_ID = "44444444-4444-4444-4444-444444444444"
ROLE_ID = "cc6305dc-7f9b-4f48-9d87-08e1c83f8e72"


def write_mock_commands(tmp_path: Path, app: dict[str, object]) -> tuple[Path, Path, Path]:
    bin_directory = tmp_path / "bin"
    bin_directory.mkdir()
    az_log = tmp_path / "az.log"
    azd_log = tmp_path / "azd.log"
    app_file = tmp_path / "app.json"
    app_file.write_text(json.dumps(app), encoding="utf-8")
    fic_list_file = tmp_path / "fic-list.json"
    fic_list_file.write_text("[]", encoding="utf-8")

    (bin_directory / "az").write_text(
        """#!/bin/sh
set -eu
printf '%s\\n' "$*" >> "$AZ_LOG"
case "$*" in
  "account show"*) printf '%s\\n' "$TENANT_ID" ;;
  "ad app show"*) cat "$AZ_APP_FILE" ;;
    "ad app create"*) cat "$AZ_APP_FILE" ;;
  "ad app update"*) : ;;
    "ad app federated-credential list"*) cat "$AZ_FIC_LIST_FILE" ;;
  "ad app federated-credential create"*)
    while [ "$#" -gt 0 ]; do
      if [ "$1" = "--parameters" ]; then
        cp "$2" "$AZ_FIC_FILE"
        break
      fi
      shift
    done
    ;;
  "ad app federated-credential show"*) cat "$AZ_FIC_EXPECTED_FILE" ;;
  *) printf '%s\\n' "unexpected az invocation: $*" >&2; exit 64 ;;
esac
""",
        encoding="utf-8",
    )
    (bin_directory / "azd").write_text(
        """#!/bin/sh
set -eu
printf '%s\\n' "$*" >> "$AZD_LOG"
case "$*" in
  "env get-values")
    cat <<'VALUES'
AZURE_ENV_NAME=demo
ENTRA_CLIENT_ID=22222222-2222-2222-2222-222222222222
ENTRA_TENANT_ID=33333333-3333-3333-3333-333333333333
API_URL=https://eda.example.test
WEB_IDENTITY_PRINCIPAL_ID=44444444-4444-4444-4444-444444444444
VALUES
    ;;
  "env set "*) : ;;
  *) printf '%s\\n' "unexpected azd invocation: $*" >&2; exit 64 ;;
esac
""",
        encoding="utf-8",
    )
    (bin_directory / "az").chmod(0o755)
    (bin_directory / "azd").chmod(0o755)
    return bin_directory, az_log, azd_log


def run_script(
    script_name: str,
    tmp_path: Path,
    app: dict[str, object],
    *,
    explicit_client_id: bool = True,
    existing_fic: bool = False,
) -> tuple[subprocess.CompletedProcess[str], Path, Path, Path]:
    bin_directory, az_log, azd_log = write_mock_commands(tmp_path, app)
    expected_fic_file = tmp_path / "expected-fic.json"
    expected_fic = {
        "name": "eda-web-demo",
        "issuer": f"https://login.microsoftonline.com/{TENANT_ID}/v2.0",
        "subject": WEB_PRINCIPAL_ID,
        "audiences": ["api://AzureADTokenExchange"],
    }
    expected_fic_file.write_text(json.dumps(expected_fic), encoding="utf-8")
    fic_list_file = tmp_path / "fic-list.json"
    fic_list_file.write_text(json.dumps([expected_fic] if existing_fic else []), encoding="utf-8")
    environment = os.environ | {
        "PATH": f"{bin_directory}:{os.environ['PATH']}",
        "AZ_LOG": str(az_log),
        "AZD_LOG": str(azd_log),
        "AZ_APP_FILE": str(tmp_path / "app.json"),
        "AZ_FIC_FILE": str(tmp_path / "fic.json"),
        "AZ_FIC_EXPECTED_FILE": str(expected_fic_file),
        "AZ_FIC_LIST_FILE": str(fic_list_file),
        "TENANT_ID": TENANT_ID,
    }
    if explicit_client_id:
        environment["EDA_ENTRA_APP_CLIENT_ID"] = CLIENT_ID
    result = subprocess.run(  # noqa: S603 -- test controls the script name and mocked executable path.
        ["/bin/sh", str(ROOT / "scripts" / script_name)],
        cwd=ROOT,
        capture_output=True,
        check=False,
        encoding="utf-8",
        env=environment,
    )
    return result, az_log, azd_log, tmp_path / "fic.json"


def test_ensure_entra_app_reuses_single_tenant_app_without_credential_mutation(tmp_path: Path) -> None:
    result, az_log, azd_log, _ = run_script(
        "ensure-entra-app.sh",
        tmp_path,
        {
            "id": APP_OBJECT_ID,
            "appId": CLIENT_ID,
            "signInAudience": "AzureADMyOrg",
            "passwordCredentials": [],
            "keyCredentials": [],
            "appRoles": [],
        },
    )

    assert result.returncode == 0, result.stderr
    assert "ad app update --id " + APP_OBJECT_ID in az_log.read_text(encoding="utf-8")
    assert "env set ENTRA_CLIENT_ID " + CLIENT_ID in azd_log.read_text(encoding="utf-8")
    assert "env set ENTRA_TENANT_ID " + TENANT_ID in azd_log.read_text(encoding="utf-8")
    assert ROLE_ID in az_log.read_text(encoding="utf-8")
    assert "secret" not in result.stdout.lower()
    assert "token" not in result.stdout.lower()


def test_ensure_entra_app_creates_a_single_tenant_app_when_no_client_id_is_supplied(tmp_path: Path) -> None:
    result, az_log, _, _ = run_script(
        "ensure-entra-app.sh",
        tmp_path,
        {
            "id": APP_OBJECT_ID,
            "appId": CLIENT_ID,
            "signInAudience": "AzureADMyOrg",
            "passwordCredentials": [],
            "keyCredentials": [],
            "appRoles": [],
        },
        explicit_client_id=False,
    )

    assert result.returncode == 0, result.stderr
    assert "ad app create --display-name eda-demo --sign-in-audience AzureADMyOrg" in az_log.read_text(encoding="utf-8")


def test_ensure_entra_app_does_not_rewrite_an_exact_existing_role(tmp_path: Path) -> None:
    result, az_log, _, _ = run_script(
        "ensure-entra-app.sh",
        tmp_path,
        {
            "id": APP_OBJECT_ID,
            "appId": CLIENT_ID,
            "signInAudience": "AzureADMyOrg",
            "passwordCredentials": [],
            "keyCredentials": [],
            "appRoles": [
                {
                    "allowedMemberTypes": ["User"],
                    "description": "Publish validated tenant branding",
                    "displayName": "Branding administrator",
                    "id": ROLE_ID,
                    "isEnabled": True,
                    "origin": "Application",
                    "value": "Branding.Admin",
                }
            ],
        },
    )

    assert result.returncode == 0, result.stderr
    assert "ad app update" not in az_log.read_text(encoding="utf-8")


def test_configure_entra_federation_writes_exact_redirect_and_fic(tmp_path: Path) -> None:
    result, az_log, _, fic_file = run_script(
        "configure-entra-federation.sh",
        tmp_path,
        {
            "id": APP_OBJECT_ID,
            "appId": CLIENT_ID,
            "signInAudience": "AzureADMyOrg",
            "passwordCredentials": [],
            "keyCredentials": [],
            "appRoles": [],
        },
    )

    assert result.returncode == 0, result.stderr
    assert "ad app update --id " + APP_OBJECT_ID in az_log.read_text(encoding="utf-8")
    assert "https://eda.example.test/api/auth/callback" in az_log.read_text(encoding="utf-8")
    assert json.loads(fic_file.read_text(encoding="utf-8")) == {
        "name": "eda-web-demo",
        "issuer": f"https://login.microsoftonline.com/{TENANT_ID}/v2.0",
        "subject": WEB_PRINCIPAL_ID,
        "description": "Trust the web UAMI as the BFF confidential client credential",
        "audiences": ["api://AzureADTokenExchange"],
    }


def test_configure_entra_federation_reuses_exact_redirect_and_fic(tmp_path: Path) -> None:
    result, az_log, _, _ = run_script(
        "configure-entra-federation.sh",
        tmp_path,
        {
            "id": APP_OBJECT_ID,
            "appId": CLIENT_ID,
            "signInAudience": "AzureADMyOrg",
            "passwordCredentials": [],
            "keyCredentials": [],
            "appRoles": [],
            "web": {"redirectUris": ["https://eda.example.test/api/auth/callback"]},
        },
        existing_fic=True,
    )

    assert result.returncode == 0, result.stderr
    observed = az_log.read_text(encoding="utf-8")
    assert "ad app update" not in observed
    assert "federated-credential delete" not in observed
    assert "federated-credential create" not in observed


def test_entra_scripts_forbid_credential_creation_and_sensitive_output() -> None:
    for script_name in ("ensure-entra-app.sh", "configure-entra-federation.sh"):
        source = (ROOT / "scripts" / script_name).read_text(encoding="utf-8")

        assert "credential reset" not in source
        assert "--password" not in source
        assert "--key-credentials" not in source
        assert "--debug" not in source
        assert "--output json" not in source
        assert "--only-show-errors" in source
        assert "set -eu" in source
    assert "mktemp" in (ROOT / "scripts" / "configure-entra-federation.sh").read_text(encoding="utf-8")
    assert "trap" in (ROOT / "scripts" / "configure-entra-federation.sh").read_text(encoding="utf-8")


def test_entra_login_location_builds_opener_and_returns_redirect() -> None:
    script = runpy.run_path(str(ROOT / "scripts" / "test-entra-app.py"))
    login_url = "https://eda.example.test/api/auth/login"
    location = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/authorize"
    headers = Message()
    headers["Location"] = location
    redirect = HTTPError(login_url, 307, "Temporary Redirect", headers, None)

    with patch.object(OpenerDirector, "open", side_effect=redirect) as open_request:
        assert script["login_location"]("https://eda.example.test") == location

    open_request.assert_called_once()
    request = open_request.call_args.args[0]
    assert request.full_url == login_url
    assert request.get_method() == "GET"
    assert open_request.call_args.kwargs == {"timeout": 15}
