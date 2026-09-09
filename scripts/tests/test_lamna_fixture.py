from __future__ import annotations

import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT / "scripts" / "fetch-lamna-fixture.py"
LOCK_PATH = ROOT / "tests" / "fixtures" / "fabric" / "lamna-healthcare" / "fixture-lock.json"


def load_fixture_module():
    specification = spec_from_file_location("fetch_lamna_fixture", SCRIPT_PATH)
    assert specification is not None
    assert specification.loader is not None
    module = module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


def test_fixture_lock_is_pinned_and_complete() -> None:
    module = load_fixture_module()

    lock = module.FixtureLock.model_validate_json(LOCK_PATH.read_text())

    assert lock.upstream_commit == "f0b76f80323a07fc6a4ca3666dd43f78d99a8418"
    assert lock.sha256 == "10e9f03a7361cdbdea5feac216b6762a5b7b22378a15893a0167c96b54dd9bf2"
    assert set(lock.files) == {
        "Hospitals.csv",
        "Departments.csv",
        "Rooms.csv",
        "Patients.csv",
        "VitalSignEquipment.csv",
        "VitalSignsReadings.csv",
    }


@pytest.mark.parametrize("url", ["http://example.test/data.zip", "https://example.test/data.csv"])
def test_download_rejects_non_pinned_https_archives(url: str) -> None:
    module = load_fixture_module()

    with pytest.raises(ValueError, match="HTTPS ZIP"):
        module.validate_archive_url(url)
