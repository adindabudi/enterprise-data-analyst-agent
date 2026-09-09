from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from shutil import which
from typing import Any, cast

from azure.core import MatchConditions
from azure.cosmos import CosmosClient
from azure.cosmos.exceptions import CosmosResourceExistsError, CosmosResourceNotFoundError
from azure.identity import AzureCliCredential
from eda_worker.model.profiles import ModelContract
from fabric_ontology_contract import build_publication_payload, read_json_object, write_json_atomically

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_AUTH_CONTRACT = ROOT / ".artifacts" / "fabric-ontology-auth-contract.json"
DEFAULT_PROVIDER_CONTRACT = ROOT / ".artifacts" / "fabric-ontology-provider-contract.json"
DEFAULT_OUTPUT = ROOT / ".artifacts" / "fabric-ontology-publication.json"
DEFAULT_MODEL_CONTRACT = ROOT / ".artifacts" / "model-contract.json"
AZURE_CLI = which("az") or "/usr/bin/az"


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare and optionally publish an immutable Fabric ontology configured-state payload."
    )
    parser.add_argument("--auth-contract", type=Path, default=DEFAULT_AUTH_CONTRACT)
    parser.add_argument("--provider-contract", type=Path, default=DEFAULT_PROVIDER_CONTRACT)
    parser.add_argument("--state", default="configured")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--model-contract", type=Path, default=DEFAULT_MODEL_CONTRACT)
    parser.add_argument("--cosmos-endpoint", default=os.getenv("EDA_COSMOS_ENDPOINT"))
    parser.add_argument("--database", default=os.getenv("EDA_COSMOS_DATABASE", "enterprise-data-analyst"))
    parser.add_argument("--container", default=os.getenv("EDA_COSMOS_RUNTIME_CONTAINER", "runtime"))
    parser.add_argument("--tenant-id", default=os.getenv("AZURE_TENANT_ID"))
    parser.add_argument("--expected-principal-id", default=os.getenv("FABRIC_ACCEPTANCE_PRINCIPAL_ID"))
    parser.add_argument("--deployment-id", default=os.getenv("EDA_DEPLOYMENT_ID"))
    parser.add_argument("--publish", action="store_true")
    return parser.parse_args()


def publish(arguments: argparse.Namespace, payload: dict[str, object], contract: dict[str, object]) -> None:
    required = (arguments.tenant_id, arguments.expected_principal_id, arguments.deployment_id)
    if not all(isinstance(value, str) and value for value in required):
        raise ValueError("ontology Cosmos publication configuration is incomplete")
    principal = subprocess.run(  # noqa: S603
        [AZURE_CLI, "ad", "signed-in-user", "show", "--query", "id", "--output", "tsv", "--only-show-errors"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if principal.casefold() != cast(str, arguments.expected_principal_id).casefold():
        raise ValueError("active principal is not the Fabric acceptance principal")
    model = ModelContract.model_validate_json(arguments.model_contract.read_text(encoding="utf-8"))
    expected_deployment_hash = hashlib.sha256(model.deployment.encode()).hexdigest()
    if contract.get("deploymentSha256") != expected_deployment_hash:
        raise ValueError("ontology provider contract deployment does not match the active model contract")
    digest = cast(str, payload["providerContractSha256"])
    contract_id = cast(str, payload["id"])
    now = datetime.now(UTC).isoformat()
    immutable = {
        "id": contract_id,
        "recordType": "featureContract",
        "provider": "ontology",
        "immutable": True,
        "providerContractSha256": digest,
        "contract": contract,
        "createdAt": now,
    }
    feature = {
        "id": "feature:fabric-ontology",
        "provider": "ontology",
        "state": "configured",
        "deploymentId": arguments.deployment_id,
        "providerContractSha256": digest,
        "modelProfile": model.model_profile.value,
        "modelDeployment": model.deployment,
        "servedModel": model.base_model,
        "servedSnapshot": model.base_model_snapshot,
        "promptVersion": model.prompt_version,
        "promptSha256": model.prompt_sha256,
        "requestOptionsSha256": model.request_options_sha256,
        "acceptanceEvidenceSha256": None,
        "verifiedAt": now,
    }
    credential = AzureCliCredential(tenant_id=cast(str, arguments.tenant_id))
    cosmos = CosmosClient(cast(str, arguments.cosmos_endpoint), credential=credential)
    try:
        container = cosmos.get_database_client(cast(str, arguments.database)).get_container_client(
            cast(str, arguments.container)
        )
        try:
            container.create_item(immutable)
        except CosmosResourceExistsError:
            existing = cast(dict[str, Any], container.read_item(contract_id, partition_key=contract_id))
            comparable = {key: existing.get(key) for key in immutable if key != "createdAt"}
            requested = {key: value for key, value in immutable.items() if key != "createdAt"}
            if not hmac.compare_digest(_canonical(comparable), _canonical(requested)):
                raise ValueError("immutable ontology contract ID contains different bytes") from None
        try:
            existing_feature = cast(
                dict[str, Any],
                container.read_item("feature:fabric-ontology", partition_key="feature:fabric-ontology"),
            )
        except CosmosResourceNotFoundError:
            container.create_item(feature)
        else:
            etag = existing_feature.get("_etag")
            if not isinstance(etag, str):
                raise ValueError("ontology feature record has no ETag")
            container.replace_item(
                item="feature:fabric-ontology",
                body=feature,
                etag=etag,
                match_condition=MatchConditions.IfNotModified,
            )
    finally:
        cosmos.close()
        credential.close()


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def main() -> int:
    arguments = parse_arguments()
    try:
        auth_contract = read_json_object(arguments.auth_contract)
        provider_contract = read_json_object(arguments.provider_contract)
        payload = build_publication_payload(
            auth_contract=auth_contract,
            provider_contract=provider_contract,
            state=arguments.state,
        )
        write_json_atomically(arguments.output, payload)
        if arguments.publish:
            if not arguments.cosmos_endpoint:
                raise ValueError("--publish requires --cosmos-endpoint or EDA_COSMOS_ENDPOINT")
            publish(arguments, payload, provider_contract)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        arguments.output.unlink(missing_ok=True)
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print(f"PASS: prepared immutable Fabric ontology configured payload {payload['providerContractSha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
