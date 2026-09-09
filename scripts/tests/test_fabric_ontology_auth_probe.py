from __future__ import annotations

import hashlib
import json
import stat
import subprocess
import sys
from pathlib import Path

from eda_worker.fabric.ontology.config import ONTOLOGY_ENDPOINT_TEMPLATE
from eda_worker.fabric.ontology.mcp_client import EXPECTED_ONTOLOGY_TOOLS, EXPECTED_TOOL_DESCRIPTIONS
from eda_worker.fabric.ontology.readiness import build_tool_manifest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/probe-fabric-ontology-auth.py"


def report() -> dict[str, object]:
    manifest = build_tool_manifest(
        [
            {
                "name": name,
                "description": EXPECTED_TOOL_DESCRIPTIONS[name],
                "inputSchema": EXPECTED_ONTOLOGY_TOOLS[name],
            }
            for name in sorted(EXPECTED_ONTOLOGY_TOOLS)
        ]
    )
    return {
        "schemaVersion": 1,
        "provider": "ontology",
        "outcome": "success",
        "proofKind": "production_bff",
        "referenceDiagnostic": False,
        "tenantTopology": "cross_tenant",
        "deploymentSha256": "a" * 64,
        "endpointTemplateSha256": hashlib.sha256(ONTOLOGY_ENDPOINT_TEMPLATE.encode()).hexdigest(),
        "targetCatalogSha256": "b" * 64,
        "scopeSha256": "c" * 64,
        "audienceSha256": "d" * 64,
        "toolNames": sorted(EXPECTED_ONTOLOGY_TOOLS),
        "toolContractSha256": manifest.contract_digest,
        "runSha256": "e" * 64,
    }


def invoke(report_path: Path, output: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 -- fixed repository auth probe.
        [sys.executable, str(SCRIPT), "--report", str(report_path), "--output", str(output), *extra],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def test_production_bff_proof_writes_strict_hash_only_contract(tmp_path: Path) -> None:
    source = tmp_path / "proof.json"
    source.write_text(json.dumps(report()), encoding="utf-8")
    source.chmod(0o600)
    output = tmp_path / "contract.json"

    result = invoke(source, output)

    assert result.returncode == 0, result.stderr
    assert json.loads(output.read_text(encoding="utf-8")) == report()
    assert stat.S_IMODE(source.stat().st_mode) == 0o600


def test_reference_scope_or_nonproduction_proof_never_writes_contract(tmp_path: Path) -> None:
    source = tmp_path / "proof.json"
    source.write_text(json.dumps(report()), encoding="utf-8")
    source.chmod(0o600)
    output = tmp_path / "contract.json"

    reference = invoke(source, output, "--reference-cli-scope")
    assert reference.returncode != 0
    assert not output.exists()

    invalid = report()
    invalid["proofKind"] = "reference_cli"
    source.write_text(json.dumps(invalid), encoding="utf-8")
    rejected = invoke(source, output)
    assert rejected.returncode != 0
    assert "PRODUCTION_AUTH_CONTRACT_UNPROVEN" in rejected.stderr
    assert not output.exists()
