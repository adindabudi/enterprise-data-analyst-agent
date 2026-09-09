from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCTOR = ROOT / "scripts/doctor.sh"

STUBS = {
    "az": '#!/bin/sh\ncase "$*" in\n  "version"*) printf "2.90.0\\n" ;;\n  "bicep version"*) exit 0 ;;\n  *) exit 0 ;;\nesac\n',
    "azd": '#!/bin/sh\nprintf "azd version 1.99.0\\n"\n',
    "node": '#!/bin/sh\nprintf "v22.0.0\\n"\n',
    "npm": '#!/bin/sh\nprintf "11.0.0\\n"\n',
    "docker": "#!/bin/sh\nexit 0\n",
    "jq": "#!/bin/sh\nexit 0\n",
    "uv": "#!/bin/sh\nexit 0\n",
}


def environment(tmp_path: Path, **overrides: str) -> dict[str, str]:
    bin_path = tmp_path / "bin"
    bin_path.mkdir(exist_ok=True)
    for name, body in STUBS.items():
        stub = bin_path / name
        stub.write_text(body, encoding="utf-8")
        stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    values = {
        "PATH": f"{bin_path}:{os.environ['PATH']}",
        "EDA_DOCTOR_NONINTERACTIVE": "1",
        "AZURE_SUBSCRIPTION_ID": "11111111-1111-1111-1111-111111111111",
        "AZURE_TENANT_ID": "22222222-2222-2222-2222-222222222222",
        "AZURE_LOCATION": "southeastasia",
        "AZURE_MONTHLY_BUDGET_AMOUNT": "100",
        "EDA_MODEL_PROFILE": "gpt-5.6-terra-medium-v1",
        "EDA_DEFENDER_CONFIRMED": "true",
        "EDA_SANDBOXES_PREVIEW_CONFIRMED": "true",
        "EDA_REDIS_SKU_CONFIRMED": "true",
    }
    values.update(overrides)
    return values


def run(environment_values: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 -- fixed repository preflight script.
        [str(DOCTOR)], env=environment_values, capture_output=True, text=True, check=False
    )


def test_fabric_enabled_does_not_require_a_separate_worker_flag(tmp_path: Path) -> None:
    result = run(environment(tmp_path, FABRIC_ENABLED="true"))

    assert "FABRIC_RUNTIME_ENABLED" not in result.stderr


def test_doctor_does_not_read_the_retired_fabric_runtime_flag() -> None:
    source = DOCTOR.read_text(encoding="utf-8")

    assert "FABRIC_RUNTIME_ENABLED" not in source


def test_fabric_left_off_reaches_the_resource_checks(tmp_path: Path) -> None:
    result = run(environment(tmp_path))

    assert "FABRIC_RUNTIME_ENABLED" not in result.stderr


def test_doctor_requires_sandbox_preview_acknowledgement_without_legacy_runtime_flags(tmp_path: Path) -> None:
    source = DOCTOR.read_text(encoding="utf-8")

    assert "EDA_SANDBOXES_PREVIEW_CONFIRMED" in source
    assert "EDA_DYNAMIC_SESSIONS_CONFIRMED" not in source
    assert "EDA_DTS_SKU_CONFIRMED" not in source
    assert "require_minimum_version" in source
    assert "'1.31.1'" in source
    assert "docker buildx" not in source
