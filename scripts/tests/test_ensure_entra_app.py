from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "ensure-entra-app.sh"
ROLE_ID = "cc6305dc-7f9b-4f48-9d87-08e1c83f8e72"
CLIENT_ID = "049efdeb-343d-42fe-aeff-7f98b8140742"
OBJECT_ID = "94e3f71c-40f3-4c08-8c60-b36c11b57dcd"
TENANT_ID = "11111111-1111-1111-1111-111111111111"


def executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


@pytest.mark.parametrize("existing_role", [True, False])
def test_graph_origin_field_does_not_invalidate_stable_branding_role(tmp_path: Path, existing_role: bool) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    capture = tmp_path / "roles.json"
    app = {
        "id": OBJECT_ID,
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
    }
    if not existing_role:
        app["appRoles"] = []
    executable(
        bin_dir / "az",
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "$1 $2" == "account show" ]]; then printf '%s\n' "$FAKE_TENANT_ID"; exit 0; fi
if [[ "$1 $2 $3" == "ad app show" ]]; then cat "$FAKE_APP_JSON"; exit 0; fi
if [[ "$1 $2 $3" == "ad app update" ]]; then
  while [[ $# -gt 0 ]]; do
    if [[ "$1" == "--app-roles" ]]; then printf '%s' "$2" > "$FAKE_CAPTURE"; exit 0; fi
    shift
  done
fi
exit 0
""",
    )
    executable(
        bin_dir / "azd",
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "$1 $2" == "env get-values" ]]; then printf '%s\n' 'AZURE_ENV_NAME=eda-dev-sea'; fi
exit 0
""",
    )
    app_path = tmp_path / "app.json"
    app_path.write_text(json.dumps(app), encoding="utf-8")
    environment = os.environ.copy()
    environment.update(
        {
            "PATH": f"{bin_dir}:{environment['PATH']}",
            "EDA_ENTRA_APP_CLIENT_ID": CLIENT_ID,
            "FAKE_APP_JSON": str(app_path),
            "FAKE_CAPTURE": str(capture),
            "FAKE_TENANT_ID": TENANT_ID,
        }
    )

    result = subprocess.run(  # noqa: S603 - runs the trusted repository script under test
        [str(SCRIPT)],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    if existing_role:
        assert not capture.exists(), "the stable role must not be rewritten for a read-only Graph field"
        return
    roles = json.loads(capture.read_text(encoding="utf-8"))
    assert roles == [
        {
            "allowedMemberTypes": ["User"],
            "description": "Publish validated tenant branding",
            "displayName": "Branding administrator",
            "id": ROLE_ID,
            "isEnabled": True,
            "value": "Branding.Admin",
        }
    ]
