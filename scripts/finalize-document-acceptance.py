from __future__ import annotations

import argparse
import json
import stat
import subprocess
import sys
from pathlib import Path
from shutil import which
from typing import Any, cast
from uuid import UUID

from azure.core import MatchConditions
from azure.cosmos import CosmosClient
from azure.identity import AzureCliCredential
from document_acceptance_state import clean_cosmos_document, validate_document_promotion
from eda_worker.acceptance.documents import (
    DocumentAcceptanceObservations,
    DocumentImageContract,
    document_contract_sha256,
    document_evidence_sha256,
)
from eda_worker.documents.readiness import DocumentFeatureRecord

AZURE_CLI = which("az") or "/usr/bin/az"


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Promote Document Pack readiness from complete deployed evidence.")
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--cosmos-endpoint", required=True)
    parser.add_argument("--database", default="enterprise-data-analyst")
    parser.add_argument("--container", default="runtime")
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--expected-principal-id", required=True)
    return parser.parse_args()


def active_principal_id() -> str:
    result = subprocess.run(  # noqa: S603
        [AZURE_CLI, "ad", "signed-in-user", "show", "--query", "id", "--output", "tsv", "--only-show-errors"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def finalize(arguments: argparse.Namespace) -> str:
    observations_path = cast(Path, arguments.observations)
    if not observations_path.is_file() or stat.S_IMODE(observations_path.stat().st_mode) != 0o600:
        raise ValueError("Document Pack observations must be a mode-0600 regular file")
    observations = DocumentAcceptanceObservations.model_validate_json(observations_path.read_text(encoding="utf-8"))
    expected_principal = UUID(cast(str, arguments.expected_principal_id))
    if UUID(active_principal_id()) != expected_principal:
        raise ValueError("active principal does not match the Document Pack acceptance principal")
    credential = AzureCliCredential(tenant_id=cast(str, arguments.tenant_id))
    cosmos = CosmosClient(cast(str, arguments.cosmos_endpoint), credential=credential)
    try:
        container = cosmos.get_database_client(cast(str, arguments.database)).get_container_client(
            cast(str, arguments.container)
        )
        feature_raw = cast(
            dict[str, Any],
            container.read_item(item="feature:documents", partition_key="feature:documents"),
        )
        feature = DocumentFeatureRecord.model_validate(clean_cosmos_document(feature_raw))
        contract_id = f"feature-contract:documents:{feature.contract_sha256}"
        contract_raw = cast(dict[str, Any], container.read_item(item=contract_id, partition_key=contract_id))
        contract_payload = contract_raw.get("contract")
        if (
            contract_raw.get("immutable") is not True
            or contract_raw.get("feature") != "documents"
            or contract_raw.get("contractSha256") != feature.contract_sha256
            or not isinstance(contract_payload, dict)
        ):
            raise ValueError("immutable Document Pack contract does not match the feature record")
        contract = DocumentImageContract.model_validate(contract_payload)
        if document_contract_sha256(contract) != feature.contract_sha256:
            raise ValueError("immutable Document Pack contract bytes do not match its digest")
        promoted = validate_document_promotion(feature=feature, contract=contract, observations=observations)
        if promoted != feature:
            etag = feature_raw.get("_etag")
            if not isinstance(etag, str) or not etag:
                raise ValueError("Document Pack feature record has no ETag")
            container.replace_item(
                item="feature:documents",
                body=promoted.model_dump(mode="json", by_alias=True),
                etag=etag,
                match_condition=MatchConditions.IfNotModified,
            )
        return document_evidence_sha256(observations)
    finally:
        cosmos.close()
        credential.close()


def main() -> int:
    arguments = parse_arguments()
    try:
        digest = finalize(arguments)
    except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError):
        print("FAIL: Document Pack readiness finalization failed", file=sys.stderr)
        return 1
    print(f"PASS: Document Pack readiness promoted evidence_sha256={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
