from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HOOK = ROOT / "scripts/run-fabric-provider-hook.sh"
CATALOG = '{"lamna-healthcare":{"workspaceId":"a","ontologyId":"b"}}'


def run(phase: str, **settings: str) -> subprocess.CompletedProcess[str]:
    environment = {
        name: value
        for name, value in os.environ.items()
        if name not in {"FABRIC_ENABLED", "FABRIC_PROVIDER", "FABRIC_ONTOLOGIES_JSON"}
    }
    environment.update(settings)
    return subprocess.run(  # noqa: S603 -- fixed repository hook script.
        [str(HOOK), phase],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )


def test_disabled_fabric_skips_the_provider_check() -> None:
    result = run("preprovision", FABRIC_ENABLED="false")

    assert result.returncode == 0
    assert result.stdout.strip() == "SKIP: Fabric intentionally disabled"


def test_ontology_intent_with_a_catalog_passes() -> None:
    result = run("preprovision", FABRIC_ENABLED="true", FABRIC_PROVIDER="ontology", FABRIC_ONTOLOGIES_JSON=CATALOG)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "PASS: ontology provider intent validated"


@pytest.mark.parametrize("catalog", ["", "{}"])
def test_ontology_intent_requires_a_catalog(catalog: str) -> None:
    result = run("preprovision", FABRIC_ENABLED="true", FABRIC_PROVIDER="ontology", FABRIC_ONTOLOGIES_JSON=catalog)

    assert result.returncode != 0
    assert "FABRIC_ONTOLOGIES_JSON" in result.stderr


@pytest.mark.parametrize("provider", ["", "unknown"])
def test_enabled_fabric_requires_the_ontology_provider(provider: str) -> None:
    result = run("preprovision", FABRIC_ENABLED="true", FABRIC_PROVIDER=provider, FABRIC_ONTOLOGIES_JSON=CATALOG)

    assert result.returncode != 0


def test_fabric_flag_must_be_boolean() -> None:
    result = run("preprovision", FABRIC_ENABLED="yes")

    assert result.returncode != 0


@pytest.mark.parametrize("phase", ["", "postprovision", "postdeploy"])
def test_only_the_preprovision_phase_exists(phase: str) -> None:
    result = run(phase, FABRIC_ENABLED="true", FABRIC_PROVIDER="ontology", FABRIC_ONTOLOGIES_JSON=CATALOG)

    assert result.returncode != 0
