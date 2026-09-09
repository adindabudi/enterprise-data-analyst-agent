from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CLOUD_DIRECTORIES = ("apps/api/tests/cloud", "packages/runtime-state/tests/cloud")
COSMOS_SETTINGS = (
    "EDA_COSMOS_ENDPOINT",
    "EDA_COSMOS_DATABASE",
    "EDA_COSMOS_AUTH_CONTAINER",
    "EDA_COSMOS_WORKSPACE_CONTAINER",
    "EDA_COSMOS_RUNTIME_CONTAINER",
)
BLOB_SETTINGS = ("EDA_BLOB_ACCOUNT_URL", "EDA_BLOB_QUARANTINE_CONTAINER")


def storage_gate_arguments() -> list[str]:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    recipe = makefile.split("deployed-storage-gate:\n", 1)[1].split("\n\n", 1)[0]
    return shlex.split(recipe)


@pytest.fixture
def gate_environment() -> dict[str, str]:
    return {
        "PATH": os.environ["PATH"],
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        **dict.fromkeys(COSMOS_SETTINGS, "local-runner-test-setting"),
    }


def run_storage_gate(tmp_path: Path, source: str, environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    (tmp_path / "pytest.ini").write_text(
        "[pytest]\naddopts = --import-mode=importlib\nmarkers = cloud: isolated runner regression\n",
        encoding="utf-8",
    )
    for directory in CLOUD_DIRECTORIES:
        suite = tmp_path / directory
        suite.mkdir(parents=True)
        (suite / "test_runner_fixture.py").write_text(source, encoding="utf-8")

    arguments = storage_gate_arguments()
    assert arguments[:2] == ["uv", "run"]
    if arguments[2] == "pytest":
        command = [sys.executable, "-m", *arguments[2:]]
    else:
        assert arguments[2] == "python"
        command = [sys.executable, str(ROOT / arguments[3]), *arguments[4:]]
    return subprocess.run(  # noqa: S603 -- repository-owned gate with isolated local test suites, no Azure credentials.
        command,
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_storage_gate_references_existing_cloud_test_directories() -> None:
    directories = {argument for argument in storage_gate_arguments() if "/tests/" in argument}

    assert set(CLOUD_DIRECTORIES) <= directories
    for directory in directories:
        assert (ROOT / directory).is_dir(), f"storage gate references missing test directory: {directory}"


@pytest.mark.parametrize("missing", [None, *COSMOS_SETTINGS])
def test_storage_gate_fails_before_running_tests_when_configuration_is_missing(
    tmp_path: Path, gate_environment: dict[str, str], missing: str | None
) -> None:
    for setting in COSMOS_SETTINGS if missing is None else (missing,):
        gate_environment.pop(setting)
    result = run_storage_gate(
        tmp_path,
        'import pytest\n@pytest.mark.cloud\ndef test_must_not_run():\n    pytest.fail("test body ran")\n',
        gate_environment,
    )
    output = result.stdout + result.stderr

    assert result.returncode != 0
    assert (missing or "EDA_COSMOS_ENDPOINT") in output
    assert "private" in output
    assert "Azure CLI" in output
    assert "test body ran" not in output
    assert "local-runner-test-setting" not in output


@pytest.mark.parametrize("missing", BLOB_SETTINGS)
def test_storage_gate_requires_blob_configuration_when_defender_is_enabled(
    tmp_path: Path, gate_environment: dict[str, str], missing: str
) -> None:
    gate_environment.update(dict.fromkeys(BLOB_SETTINGS, "local-runner-test-setting"))
    gate_environment["EDA_RUN_DEFENDER_ACCEPTANCE"] = "1"
    gate_environment.pop(missing)
    result = run_storage_gate(
        tmp_path, "import pytest\n@pytest.mark.cloud\ndef test_cloud_fixture():\n    assert True\n", gate_environment
    )

    assert result.returncode != 0
    assert missing in result.stdout + result.stderr


@pytest.mark.parametrize(
    "source",
    [
        'import pytest\n@pytest.mark.cloud\ndef test_cloud_fixture():\n    pytest.skip("local skip")\n',
        'import pytest\npytest.skip("collection skip", allow_module_level=True)\n',
        'import pytest\n@pytest.mark.cloud\n@pytest.mark.xfail(reason="local xfail")\ndef test_cloud_fixture():\n    assert False\n',
        "def test_non_cloud_fixture():\n    assert True\n",
    ],
    ids=["all-skipped", "collection-skipped", "all-xfailed", "no-cloud-tests"],
)
def test_storage_gate_rejects_runs_without_passing_cloud_tests(
    tmp_path: Path, gate_environment: dict[str, str], source: str
) -> None:
    result = run_storage_gate(tmp_path, source, gate_environment)

    assert result.returncode != 0
    assert "no cloud tests passed" in result.stdout + result.stderr


def test_storage_gate_accepts_executed_cloud_tests_with_an_optional_skip(
    tmp_path: Path, gate_environment: dict[str, str]
) -> None:
    result = run_storage_gate(
        tmp_path,
        "import pytest\npytestmark = pytest.mark.cloud\ndef test_cloud_fixture():\n    assert True\n"
        'def test_optional_fixture():\n    pytest.skip("optional acceptance not enabled")\n',
        gate_environment,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "2 passed" in result.stdout
    assert "2 skipped" in result.stdout


@pytest.mark.parametrize(
    ("source", "exit_code"),
    [
        ("import pytest\n@pytest.mark.cloud\ndef test_cloud_fixture():\n    assert False\n", 1),
        ('raise RuntimeError("local collection failure")\n', 2),
    ],
    ids=["failed-test", "collection-error"],
)
def test_storage_gate_preserves_pytest_failures(
    tmp_path: Path, gate_environment: dict[str, str], source: str, exit_code: int
) -> None:
    result = run_storage_gate(tmp_path, source, gate_environment)

    assert result.returncode == exit_code, result.stdout + result.stderr
