from __future__ import annotations

import base64
import secrets
import shutil
import string
import subprocess
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
EMULATOR_TEST = Path("apps/api/tests/integration/test_azurite_uploads.py")


@pytest.fixture
def scanner() -> str:
    executable = shutil.which("gitleaks")
    if executable is None:
        pytest.skip("Gitleaks 8.30.1 is required for secret-scanner regressions")
    return executable


def scan(scanner: str, directory: Path) -> int:
    result = subprocess.run(  # noqa: S603
        [
            scanner,
            "dir",
            str(directory),
            "--config",
            str(ROOT / ".gitleaks.toml"),
            "--no-banner",
            "--redact=100",
            "--exit-code=42",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode in {0, 42}, result.stderr
    return result.returncode


def test_detects_a_synthetic_token_with_default_rules(scanner: str, tmp_path: Path) -> None:
    token = "ghp_" + "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(36))
    (tmp_path / "canary.js").write_text(f'const syntheticToken = "{token}";\n', encoding="utf-8")

    assert scan(scanner, tmp_path) == 42


def test_azurite_exception_does_not_hide_other_credentials(scanner: str, tmp_path: Path) -> None:
    emulator = tmp_path / EMULATOR_TEST
    emulator.parent.mkdir(parents=True)
    source = (ROOT / EMULATOR_TEST).read_text(encoding="utf-8")
    emulator.write_text(source, encoding="utf-8")

    assert scan(scanner, tmp_path) == 0

    token = "ghp_" + "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(36))
    emulator.write_text(source + f'\nother_token = "{token}"\n', encoding="utf-8")
    assert scan(scanner, tmp_path) == 42

    policy = tomllib.loads((ROOT / ".gitleaks.toml").read_text(encoding="utf-8"))
    exception = policy["allowlists"][1]
    assert exception["condition"] == "AND"
    public_key = exception["regexes"][0].removeprefix("^").removesuffix("$")
    assert public_key in source
    different_key = base64.b64encode(secrets.token_bytes(64)).decode()
    emulator.write_text(source.replace(public_key, different_key), encoding="utf-8")
    assert scan(scanner, tmp_path) == 42
