from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import pytest
from eda_api.readiness.models import DocumentFeatureState
from eda_sandbox.contracts import ValidationProfile as SandboxValidationProfile
from eda_worker.acceptance.documents import DOCUMENT_KINDS, DocumentAcceptanceObservations, DocumentImageContract
from eda_worker.documents.readiness import SKILL_NAMES
from eda_worker.sandbox.benchmark import BenchmarkFailure, SessionMetric, evaluate_benchmark
from eda_worker.tools.contracts import ValidationProfile as ToolValidationProfile
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[2]


def load_script(relative: str, name: str) -> ModuleType:
    specification = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[name] = module
    specification.loader.exec_module(module)
    return module


document_skills_contract = load_script("scripts/document_skills_contract.py", "document_skills_contract")
DOCUMENT_SKILL_NAMES = frozenset(document_skills_contract.DOCUMENT_SKILL_NAMES)  # type: ignore[attr-defined]
WEB_SKILL_NAMES = frozenset(document_skills_contract.WEB_SKILL_NAMES)  # type: ignore[attr-defined]
LOCK_SKILLS = frozenset(
    entry["name"] for entry in json.loads((ROOT / "skills.lock.json").read_text(encoding="utf-8"))["skills"]
)
LOCK_DOCUMENT_SKILLS = LOCK_SKILLS & DOCUMENT_SKILL_NAMES


def digests() -> dict[str, str]:
    return {name: f"{index:064d}" for index, name in enumerate(sorted(LOCK_SKILLS), start=1)}


def session(*, kind: str, ordinal: int) -> SessionMetric:
    zeroed = dict.fromkeys(
        (
            "allocation_ms",
            "first_health_ms",
            "import_ms",
            "execution_ms",
            "render_ms",
            "validation_ms",
            "stop_ms",
            "peak_rss_bytes",
            "cpu_time_ms",
            "output_bytes",
            "output_files",
        ),
        0,
    )
    flags = dict.fromkeys(
        ("oom", "process_escape", "file_escape", "network_success", "cross_session_leakage", "lingering_after_stop"),
        False,
    )
    return SessionMetric(kind=kind, ordinal=ordinal, completed=True, **zeroed, **flags)  # type: ignore[arg-type]


def test_every_declared_skill_set_matches_the_lockfile() -> None:
    bundle_builder = load_script("services/worker/document-skills/build-bundles.py", "build_bundles")
    benchmark = load_script("scripts/run-sandbox-benchmark.py", "benchmark_skill_set")

    declarations = {
        "worker runtime": frozenset(SKILL_NAMES),
        "worker acceptance": DOCUMENT_KINDS,
        "bundle builder": frozenset(bundle_builder.SKILL_NAMES),  # type: ignore[attr-defined]
        "benchmark waves": frozenset(benchmark.DOCUMENT_TYPES),  # type: ignore[attr-defined]
    }

    assert {name: value for name, value in declarations.items() if value != LOCK_DOCUMENT_SKILLS} == {}
    assert LOCK_SKILLS - LOCK_DOCUMENT_SKILLS == WEB_SKILL_NAMES


@pytest.mark.parametrize("profiles", [SandboxValidationProfile, ToolValidationProfile])
def test_every_lockfile_skill_has_a_document_validation_profile(profiles: type) -> None:
    assert {name for name in LOCK_DOCUMENT_SKILLS if not hasattr(profiles, f"DOCUMENT_{name.upper()}")} == set()


def test_readiness_and_contract_accept_exactly_the_lockfile_skills() -> None:
    bundles = {name: digest for name, digest in digests().items() if name in LOCK_DOCUMENT_SKILLS}
    common = {
        "deploymentId": "deployment-1",
        "workerImageDigest": f"sha256:{'a' * 64}",
        "sandboxImageDigest": f"sha256:{'e' * 64}",
        "lockSha256": "b" * 64,
        "commit": "c" * 40,
    }

    DocumentImageContract.model_validate({**common, "bundles": bundles})
    DocumentFeatureState.model_validate(
        {
            **common,
            "bundles": bundles,
            "state": "configured",
            "contractSha256": "d" * 64,
            "verifiedAt": datetime(2026, 7, 31, tzinfo=UTC).isoformat(),
        }
    )

    with pytest.raises(ValidationError):
        DocumentImageContract.model_validate({**common, "bundles": {**bundles, "extra": "f" * 64}})


def test_one_benchmark_document_session_is_required_per_lockfile_skill() -> None:
    sessions = [session(kind="core", ordinal=index) for index in range(10)]
    sessions += [session(kind="document", ordinal=index) for index in range(len(LOCK_DOCUMENT_SKILLS))]

    assert evaluate_benchmark(sessions).document_sessions == len(LOCK_DOCUMENT_SKILLS)
    assert DocumentAcceptanceObservations.model_fields["document_sessions"].metadata[0].ge == len(LOCK_DOCUMENT_SKILLS)

    with pytest.raises(BenchmarkFailure):
        evaluate_benchmark(sessions[:-1])
