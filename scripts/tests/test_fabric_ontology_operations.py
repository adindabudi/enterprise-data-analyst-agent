from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
RUNBOOK = ROOT / "docs" / "runbooks" / "fabric-ontology-lab.md"
DOCTOR = ROOT / "scripts" / "doctor-fabric-ontology.sh"
CLEANUP = ROOT / "scripts" / "cleanup-fabric-ontology-lab.sh"
WORKSPACE_ID = "11111111-1111-1111-1111-111111111111"
TENANT_ID = "22222222-2222-2222-2222-222222222222"
WORKSPACE_NAME = "eda-ontology-acceptance-test"


def write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


@pytest.fixture
def cleanup_environment(tmp_path: Path) -> tuple[dict[str, str], Path, Path]:
    bin_directory = tmp_path / "bin"
    bin_directory.mkdir()
    curl_log = tmp_path / "curl.log"
    curl_state = tmp_path / "curl-state"
    manifest_path = tmp_path / "ownership-manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "workspaceId": WORKSPACE_ID,
                "tenantId": TENANT_ID,
                "workspaceName": WORKSPACE_NAME,
                "fixture": "lamna-healthcare",
                "managedBy": "enterprise-data-analyst",
            }
        ),
        encoding="utf-8",
    )

    write_executable(
        bin_directory / "az",
        "#!/bin/sh\n"
        'if [ "$1" = account ] && [ "$2" = get-access-token ]; then\n'
        '  printf \'%s\\n\' \'{"accessToken":"fake-token","tenant":"22222222-2222-2222-2222-222222222222"}\'\n'
        "  exit 0\n"
        "fi\n"
        "exit 1\n",
    )
    write_executable(
        bin_directory / "curl",
        "#!/bin/sh\n"
        'printf \'%s\\n\' "$*" >> "$FABRIC_TEST_CURL_LOG"\n'
        "output=\nmethod=GET\n"
        'while [ "$#" -gt 0 ]; do\n'
        '  case "$1" in\n'
        "    -o) output=$2; shift 2 ;;\n"
        "    -X) method=$2; shift 2 ;;\n"
        "    *) shift ;;\n"
        "  esac\n"
        "done\n"
        'if [ "$method" = DELETE ]; then\n'
        '  : > "$FABRIC_TEST_CURL_STATE"\n'
        "  [ -n \"$output\" ] && printf '%s' '{}' > \"$output\"\n"
        "  printf '%s' 202\n"
        'elif [ -e "$FABRIC_TEST_CURL_STATE" ]; then\n'
        "  [ -n \"$output\" ] && printf '%s' '{}' > \"$output\"\n"
        "  printf '%s' 404\n"
        "else\n"
        '  [ -n "$output" ] && printf \'%s\' "$FABRIC_TEST_WORKSPACE" > "$output"\n'
        "  printf '%s' 200\n"
        "fi\n",
    )

    environment = os.environ.copy()
    environment.update(
        {
            "PATH": f"{bin_directory}:{environment['PATH']}",
            "FABRIC_ONTOLOGY_TENANT_ID": TENANT_ID,
            "FABRIC_TEST_CURL_LOG": str(curl_log),
            "FABRIC_TEST_CURL_STATE": str(curl_state),
            "FABRIC_TEST_WORKSPACE": json.dumps(
                {"id": WORKSPACE_ID, "displayName": WORKSPACE_NAME, "tenantId": TENANT_ID}
            ),
        }
    )
    return environment, manifest_path, curl_log


def run_cleanup(environment: dict[str, str], *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 -- invokes the fixed repository cleanup script with test-controlled inputs.
        [str(CLEANUP), *arguments],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )


def test_runbook_records_external_fixture_setup_and_cleanup_boundaries() -> None:
    content = RUNBOOK.read_text(encoding="utf-8")

    for required_text in (
        "eda-ontology-acceptance-<suffix>",
        "tenant D",
        "F2+",
        "P1+",
        "fetch-lamna-fixture.py",
        "LamnaHealthcareLH",
        "LamnaHealthcareEH",
        "LamnaHealthcareModel",
        "LamnaHealthcareOntology",
        "five lakehouse tables",
        "20-row",
        "four Direct Lake relationships",
        "five entity keys",
        "four relationship bindings",
        "VitalSignEquipment",
        "published",
        "allowed",
        "denied",
        "workspace cleanup before capacity cleanup",
        "PHI",
        "Bicep",
        "Terraform",
        "resource group",
    ):
        assert required_text.casefold() in content.casefold()


def test_doctor_is_read_only_and_uses_an_isolated_azure_cli_context() -> None:
    content = DOCTOR.read_text(encoding="utf-8")

    assert "AZURE_CONFIG_DIR" in content
    assert "mktemp -d" in content
    assert "chmod 700" in content
    assert "trap" in content
    assert "FABRIC_PROVIDER" in content
    assert "ontology" in content
    assert "get-access-token" in content
    assert "list_ontology_entity_types" in content
    assert "search_ontology" in content
    for forbidden in ("az login", "az logout", "az account set", "az group delete", "az capacity", "DELETE "):
        assert forbidden not in content


def test_cleanup_defaults_to_dry_run_without_contacting_fabric(
    cleanup_environment: tuple[dict[str, str], Path, Path],
) -> None:
    environment, _, curl_log = cleanup_environment

    result = run_cleanup(environment, "--workspace-id", WORKSPACE_ID, "--expected-name", WORKSPACE_NAME)

    assert result.returncode == 0
    assert "DRY RUN" in result.stdout
    assert not curl_log.exists()


@pytest.mark.parametrize(
    ("arguments", "workspace_response"),
    [
        (("--workspace-id", WORKSPACE_ID, "--expected-name", WORKSPACE_NAME, "--execute"), None),
        (
            (
                "--workspace-id",
                WORKSPACE_ID,
                "--confirm-workspace-id",
                "33333333-3333-3333-3333-333333333333",
                "--expected-name",
                WORKSPACE_NAME,
                "--ownership-manifest",
                "MANIFEST",
                "--execute",
            ),
            None,
        ),
        (
            (
                "--workspace-id",
                WORKSPACE_ID,
                "--confirm-workspace-id",
                WORKSPACE_ID,
                "--expected-name",
                WORKSPACE_NAME,
                "--ownership-manifest",
                "MANIFEST",
                "--execute",
            ),
            {"id": WORKSPACE_ID, "displayName": WORKSPACE_NAME, "tenantId": "33333333-3333-3333-3333-333333333333"},
        ),
        (
            (
                "--workspace-id",
                WORKSPACE_ID,
                "--confirm-workspace-id",
                WORKSPACE_ID,
                "--expected-name",
                WORKSPACE_NAME,
                "--ownership-manifest",
                "MANIFEST",
                "--execute",
            ),
            {"id": "33333333-3333-3333-3333-333333333333", "displayName": WORKSPACE_NAME, "tenantId": TENANT_ID},
        ),
        (
            (
                "--workspace-id",
                WORKSPACE_ID,
                "--confirm-workspace-id",
                WORKSPACE_ID,
                "--expected-name",
                "other-workspace",
                "--ownership-manifest",
                "MANIFEST",
                "--execute",
            ),
            None,
        ),
        (
            (
                "--workspace-id",
                WORKSPACE_ID,
                "--confirm-workspace-id",
                WORKSPACE_ID,
                "--expected-name",
                WORKSPACE_NAME,
                "--ownership-manifest",
                "MANIFEST",
                "--resource-group",
                "not-allowed",
                "--execute",
            ),
            None,
        ),
        (
            (
                "--workspace-id",
                WORKSPACE_ID,
                "--confirm-workspace-id",
                WORKSPACE_ID,
                "--expected-name",
                WORKSPACE_NAME,
                "--ownership-manifest",
                "MANIFEST",
                "--capacity-id",
                "not-allowed",
                "--execute",
            ),
            None,
        ),
    ],
)
def test_cleanup_refuses_unsafe_destructive_requests_before_delete(
    cleanup_environment: tuple[dict[str, str], Path, Path],
    arguments: tuple[str, ...],
    workspace_response: dict[str, str] | None,
) -> None:
    environment, manifest_path, curl_log = cleanup_environment
    expanded_arguments = tuple(str(manifest_path) if argument == "MANIFEST" else argument for argument in arguments)
    if workspace_response is not None:
        environment["FABRIC_TEST_WORKSPACE"] = json.dumps(workspace_response)

    result = run_cleanup(environment, *expanded_arguments)

    assert result.returncode != 0
    assert not curl_log.exists() or "-X DELETE" not in curl_log.read_text(encoding="utf-8")


def test_cleanup_refuses_a_nonmatching_ownership_manifest(
    cleanup_environment: tuple[dict[str, str], Path, Path],
) -> None:
    environment, manifest_path, curl_log = cleanup_environment
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["fixture"] = "other-fixture"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = run_cleanup(
        environment,
        "--workspace-id",
        WORKSPACE_ID,
        "--confirm-workspace-id",
        WORKSPACE_ID,
        "--expected-name",
        WORKSPACE_NAME,
        "--ownership-manifest",
        str(manifest_path),
        "--execute",
    )

    assert result.returncode != 0
    assert not curl_log.exists()


def test_cleanup_deletes_only_the_confirmed_owned_workspace(
    cleanup_environment: tuple[dict[str, str], Path, Path],
) -> None:
    environment, manifest_path, curl_log = cleanup_environment

    result = run_cleanup(
        environment,
        "--workspace-id",
        WORKSPACE_ID,
        "--confirm-workspace-id",
        WORKSPACE_ID,
        "--expected-name",
        WORKSPACE_NAME,
        "--ownership-manifest",
        str(manifest_path),
        "--execute",
    )

    assert result.returncode == 0, result.stderr
    calls = curl_log.read_text(encoding="utf-8")
    assert calls.count("-X DELETE") == 1
    assert f"https://api.fabric.microsoft.com/v1/workspaces/{WORKSPACE_ID}" in calls
    assert "resourceGroups" not in calls
    assert "capacities" not in calls
