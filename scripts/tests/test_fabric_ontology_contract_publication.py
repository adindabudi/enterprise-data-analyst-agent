from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

from eda_worker.fabric.ontology.config import ONTOLOGY_ENDPOINT_TEMPLATE
from eda_worker.fabric.ontology.mcp_client import EXPECTED_ONTOLOGY_TOOLS, EXPECTED_TOOL_DESCRIPTIONS
from eda_worker.fabric.ontology.readiness import build_tool_manifest

ROOT = Path(__file__).resolve().parents[2]
CHECK_SCRIPT = ROOT / "scripts" / "check-fabric-ontology-contract.py"
PUBLISH_SCRIPT = ROOT / "scripts" / "publish-fabric-ontology-contract.py"


def digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def expected_tools() -> list[dict[str, object]]:
    return [
        {
            "name": name,
            "description": EXPECTED_TOOL_DESCRIPTIONS[name],
            "inputSchema": EXPECTED_ONTOLOGY_TOOLS[name],
        }
        for name in sorted(EXPECTED_ONTOLOGY_TOOLS)
    ]


def run_script(script: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 -- invokes a fixed repository script with temporary test fixtures.
        [sys.executable, str(script), *arguments],
        check=False,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )


def write_contract_inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    catalog = {
        "aliases": [
            {
                "alias": "lamna-healthcare",
                "description": "Synthetic hospital operations.",
                "routingTerms": ["patients", "equipment"],
                "targetPairSha256": "a" * 64,
            }
        ]
    }
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(json.dumps(catalog), encoding="utf-8")

    manifest = build_tool_manifest(expected_tools())
    auth_contract = {
        "schemaVersion": 1,
        "provider": "ontology",
        "outcome": "success",
        "proofKind": "production_bff",
        "referenceDiagnostic": False,
        "tenantTopology": "cross_tenant",
        "deploymentSha256": "b" * 64,
        "endpointTemplateSha256": hashlib.sha256(ONTOLOGY_ENDPOINT_TEMPLATE.encode()).hexdigest(),
        "targetCatalogSha256": digest(catalog),
        "scopeSha256": "c" * 64,
        "audienceSha256": "d" * 64,
        "toolNames": list(manifest.tool_names),
        "toolContractSha256": manifest.contract_digest,
        "runSha256": "e" * 64,
    }
    auth_path = tmp_path / "fabric-ontology-auth-contract.json"
    auth_path.write_text(json.dumps(auth_contract), encoding="utf-8")

    inspection = {
        "aliases": {
            "lamna-healthcare": {
                "values": [
                    {
                        "name": "Patients",
                        "id": "entity-id-must-not-publish",
                        "sourceMapping": {"table": "Patients", "clusterUri": "https://private.example"},
                        "properties": [
                            {
                                "id": "property-id-must-not-publish",
                                "name": "PatientId",
                                "valueType": "String",
                                "source": "Patients.PatientId",
                            }
                        ],
                        "entityIdParts": ["property-id-must-not-publish"],
                        "timeseriesProperties": [],
                    }
                ]
            }
        }
    }
    inspection_path = tmp_path / "inspection.json"
    inspection_path.write_text(json.dumps(inspection), encoding="utf-8")
    return auth_path, catalog_path, inspection_path


def test_check_builds_a_scrubbed_hash_only_provider_contract(tmp_path: Path) -> None:
    auth_path, catalog_path, inspection_path = write_contract_inputs(tmp_path)
    output_path = tmp_path / "fabric-ontology-provider-contract.json"

    result = run_script(
        CHECK_SCRIPT,
        "--auth-contract",
        str(auth_path),
        "--catalog",
        str(catalog_path),
        "--inspection",
        str(inspection_path),
        "--output",
        str(output_path),
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    serialized = json.dumps(payload, sort_keys=True)
    assert payload["provider"] == "ontology"
    assert payload["authContractSha256"] == digest(json.loads(auth_path.read_text(encoding="utf-8")))
    assert payload["aliases"][0]["grounding"] == {
        "entities": [
            {
                "keyProperties": ["PatientId"],
                "name": "Patients",
                "properties": [{"name": "PatientId", "valueType": "String"}],
                "timeSeriesProperties": [],
            }
        ]
    }
    for forbidden in (
        "https://private.example",
        "entity-id-must-not-publish",
        "property-id-must-not-publish",
        "sourceMapping",
    ):
        assert forbidden not in serialized


def test_check_fails_closed_for_a_reference_or_mismatched_auth_proof(tmp_path: Path) -> None:
    auth_path, catalog_path, inspection_path = write_contract_inputs(tmp_path)
    auth_contract = json.loads(auth_path.read_text(encoding="utf-8"))
    auth_contract["referenceDiagnostic"] = True
    auth_path.write_text(json.dumps(auth_contract), encoding="utf-8")

    result = run_script(
        CHECK_SCRIPT,
        "--auth-contract",
        str(auth_path),
        "--catalog",
        str(catalog_path),
        "--inspection",
        str(inspection_path),
        "--output",
        str(tmp_path / "provider.json"),
    )

    assert result.returncode != 0
    assert not (tmp_path / "provider.json").exists()


def test_publish_prepares_an_immutable_configured_payload_and_rejects_ready(tmp_path: Path) -> None:
    auth_path, catalog_path, inspection_path = write_contract_inputs(tmp_path)
    provider_path = tmp_path / "fabric-ontology-provider-contract.json"
    check = run_script(
        CHECK_SCRIPT,
        "--auth-contract",
        str(auth_path),
        "--catalog",
        str(catalog_path),
        "--inspection",
        str(inspection_path),
        "--output",
        str(provider_path),
    )
    assert check.returncode == 0, check.stderr

    payload_path = tmp_path / "publication-payload.json"
    publish = run_script(
        PUBLISH_SCRIPT,
        "--auth-contract",
        str(auth_path),
        "--provider-contract",
        str(provider_path),
        "--output",
        str(payload_path),
    )

    assert publish.returncode == 0, publish.stderr
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    assert payload["state"] == "configured"
    assert payload["immutable"] is True
    assert payload["id"] == f"feature-contract:fabric-ontology:{payload['providerContractSha256']}"

    ready = run_script(
        PUBLISH_SCRIPT,
        "--auth-contract",
        str(auth_path),
        "--provider-contract",
        str(provider_path),
        "--state",
        "ready",
        "--output",
        str(tmp_path / "ready.json"),
    )

    assert ready.returncode != 0
    assert not (tmp_path / "ready.json").exists()
