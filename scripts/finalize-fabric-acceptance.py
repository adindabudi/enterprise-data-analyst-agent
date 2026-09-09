from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from shutil import which
from typing import Any, cast

from azure.core import MatchConditions
from azure.cosmos import CosmosClient
from azure.identity import AzureCliCredential
from eda_worker.acceptance.fabric import FabricAcceptanceResult
from eda_worker.fabric.readiness import FabricFeatureRecord
from fabric_acceptance_state import (
    FabricAcceptanceManifest,
    acceptance_manifest_sha256,
    clean_cosmos_document,
    validate_promotion,
)

AZURE_CLI = which("az") or "/usr/bin/az"


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Promote Fabric readiness from complete cross-tenant evidence.")
    parser.add_argument("--manifest", type=Path, required=True)
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
    manifest = FabricAcceptanceManifest.model_validate_json(cast(Path, arguments.manifest).read_text(encoding="utf-8"))
    principal_id = active_principal_id()
    credential = AzureCliCredential(tenant_id=cast(str, arguments.tenant_id))
    cosmos = CosmosClient(cast(str, arguments.cosmos_endpoint), credential=credential)
    try:
        container = cosmos.get_database_client(cast(str, arguments.database)).get_container_client(
            cast(str, arguments.container)
        )
        feature_raw = cast(
            dict[str, Any],
            container.read_item(item="feature:fabric", partition_key="feature:fabric"),
        )
        feature = FabricFeatureRecord.model_validate(clean_cosmos_document(feature_raw))
        contract_id = f"feature-contract:fabric:{feature.provider_contract_sha256}"
        contract_raw = cast(dict[str, Any], container.read_item(item=contract_id, partition_key=contract_id))
        if (
            contract_raw.get("immutable") is not True
            or contract_raw.get("provider") != "semantic_model"
            or contract_raw.get("providerContractSha256") != manifest.provider_contract_sha256
        ):
            raise ValueError("immutable Fabric provider contract does not match acceptance evidence")
        result_id = f"fabric-acceptance-result:{manifest.run_id}"
        result_raw = cast(dict[str, Any], container.read_item(item=result_id, partition_key=result_id))
        result_payload = {
            key: value for key, value in clean_cosmos_document(result_raw).items() if key not in {"id", "recordType"}
        }
        result = FabricAcceptanceResult.model_validate(result_payload)
        promoted = validate_promotion(
            active_principal_id=principal_id,
            expected_principal_id=cast(str, arguments.expected_principal_id),
            feature=feature,
            result=result,
            manifest=manifest,
        )
        etag = feature_raw.get("_etag")
        if not isinstance(etag, str) or not etag:
            raise ValueError("Fabric feature record has no ETag")
        container.replace_item(
            item="feature:fabric",
            body=promoted.model_dump(mode="json", by_alias=True),
            etag=etag,
            match_condition=MatchConditions.IfNotModified,
        )
        input_id = f"fabric-acceptance-input:{manifest.run_id}"
        container.delete_item(item=input_id, partition_key=input_id)
        return acceptance_manifest_sha256(manifest)
    finally:
        cosmos.close()
        credential.close()


def main() -> int:
    arguments = parse_arguments()
    try:
        digest = finalize(arguments)
    except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError):
        print("FAIL: Fabric readiness finalization failed", file=sys.stderr)
        return 1
    print(f"PASS: Fabric readiness promoted evidence_sha256={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
