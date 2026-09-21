from __future__ import annotations

import importlib.util
from email.message import Message
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load_module() -> Any:
    spec = importlib.util.spec_from_file_location("generate_python_sbom", ROOT / "scripts/generate-python-sbom.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def package_metadata(version: str = "1.2.3") -> Message:
    metadata = Message()
    metadata["Name"] = "fixture-package"
    metadata["Version"] = version
    metadata["License-Expression"] = "MIT OR Apache-2.0"
    return metadata


def test_sbom_preserves_the_installed_license_expression(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_module()
    monkeypatch.setattr(module, "metadata", lambda _name: package_metadata())

    sbom = module.build_sbom("fixture-package==1.2.3\n")

    assert sbom["bomFormat"] == "CycloneDX"
    assert sbom["components"] == [
        {
            "type": "library",
            "name": "fixture-package",
            "version": "1.2.3",
            "purl": "pkg:pypi/fixture-package@1.2.3",
            "licenses": [{"expression": "MIT OR Apache-2.0"}],
        }
    ]


def test_sbom_rejects_an_environment_that_differs_from_the_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_module()
    monkeypatch.setattr(module, "metadata", lambda _name: package_metadata("2.0.0"))

    with pytest.raises(ValueError, match="version mismatch"):
        module.build_sbom("fixture-package==1.2.3\n")


def test_sbom_does_not_treat_unknown_metadata_as_mit(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_module()
    document = package_metadata()
    del document["License-Expression"]
    document["License"] = "Custom upstream terms"
    monkeypatch.setattr(module, "metadata", lambda _name: document)

    component = module.build_sbom("fixture-package==1.2.3\n")["components"][0]

    assert component["licenses"] == [{"license": {"name": "Custom upstream terms"}}]
