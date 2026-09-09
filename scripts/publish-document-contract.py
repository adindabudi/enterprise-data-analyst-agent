from __future__ import annotations

import argparse
import asyncio
import hmac
import json
import os
import stat
import subprocess
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from shutil import which
from typing import Any, cast
from uuid import UUID

from azure.core import MatchConditions
from azure.cosmos.aio import ContainerProxy, CosmosClient
from azure.cosmos.exceptions import CosmosResourceExistsError, CosmosResourceNotFoundError
from azure.identity.aio import AzureCliCredential
from eda_worker.acceptance.documents import DocumentImageContract, document_contract_sha256

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = ROOT / ".artifacts" / "document-image-contract.json"
AZURE_CLI = which("az") or "/usr/bin/az"


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Publish an immutable Document Pack image contract.")
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--cosmos-endpoint", default=os.getenv("EDA_COSMOS_ENDPOINT"))
    parser.add_argument("--database", default=os.getenv("EDA_COSMOS_DATABASE", "enterprise-data-analyst"))
    parser.add_argument("--container", default=os.getenv("EDA_COSMOS_RUNTIME_CONTAINER", "runtime"))
    parser.add_argument("--tenant-id", default=os.getenv("AZURE_TENANT_ID"))
    parser.add_argument(
        "--expected-principal-id",
        default=os.getenv("EDA_ACCEPTANCE_PRINCIPAL_ID") or os.getenv("FABRIC_ACCEPTANCE_PRINCIPAL_ID"),
    )
    arguments = parser.parse_args()
    for name in ("cosmos_endpoint", "tenant_id", "expected_principal_id"):
        if not getattr(arguments, name):
            parser.error(f"--{name.replace('_', '-')} is required")
    return arguments


async def publish(arguments: argparse.Namespace) -> str:
    tenant_id = UUID(cast(str, arguments.tenant_id))
    principal_id = UUID(cast(str, arguments.expected_principal_id))
    _verify_cli_context(tenant_id, principal_id)
    contract = DocumentImageContract.model_validate_json(cast(Path, arguments.contract).read_text(encoding="utf-8"))
    contract_payload = contract.model_dump(mode="json", by_alias=True)
    contract_digest = document_contract_sha256(contract)
    contract_id = f"feature-contract:documents:{contract_digest}"
    now = datetime.now(UTC).isoformat()
    immutable_document: dict[str, object] = {
        "id": contract_id,
        "recordType": "featureContract",
        "feature": "documents",
        "immutable": True,
        "contractSha256": contract_digest,
        "contract": contract_payload,
        "createdAt": now,
    }
    feature_document: dict[str, object] = {
        "id": "feature:documents",
        "recordType": "featureState",
        "state": "configured",
        "deploymentId": contract.deployment_id,
        "contractSha256": contract_digest,
        "workerImageDigest": contract.worker_image_digest,
        "sandboxImageDigest": contract.sandbox_image_digest,
        "lockSha256": contract.lock_sha256,
        "commit": contract.commit,
        "bundles": contract.bundles,
        "evidenceSha256": None,
        "verifiedAt": now,
    }
    credential = AzureCliCredential(tenant_id=str(tenant_id))
    client = CosmosClient(cast(str, arguments.cosmos_endpoint), credential=credential)
    container = client.get_database_client(cast(str, arguments.database)).get_container_client(
        cast(str, arguments.container)
    )
    try:
        await _create_immutable(container, immutable_document)
        await _replace_feature(container, feature_document)
        return contract_digest
    finally:
        await client.close()
        await credential.close()


async def _create_immutable(container: ContainerProxy, document: dict[str, object]) -> None:
    identifier = cast(str, document["id"])
    try:
        await container.create_item(document)
        return
    except CosmosResourceExistsError:
        existing = cast(dict[str, Any], await container.read_item(identifier, partition_key=identifier))
    comparable = {"id", "recordType", "feature", "immutable", "contractSha256", "contract"}
    if not hmac.compare_digest(
        _canonical_json({key: existing.get(key) for key in comparable}),
        _canonical_json({key: document.get(key) for key in comparable}),
    ):
        raise ValueError("immutable Document Pack contract ID contains different bytes")


async def _replace_feature(container: ContainerProxy, document: dict[str, object]) -> None:
    identifier = cast(str, document["id"])
    try:
        existing = cast(dict[str, Any], await container.read_item(identifier, partition_key=identifier))
    except CosmosResourceNotFoundError:
        try:
            await container.create_item(document)
            return
        except CosmosResourceExistsError:
            existing = cast(dict[str, Any], await container.read_item(identifier, partition_key=identifier))
    if existing.get("state") == "ready" and existing.get("contractSha256") == document.get("contractSha256"):
        return
    etag = existing.get("_etag")
    if not isinstance(etag, str) or not etag:
        raise ValueError("Document Pack feature record has no ETag")
    await container.replace_item(
        item=identifier,
        body=document,
        etag=etag,
        match_condition=MatchConditions.IfNotModified,
    )


def _verify_cli_context(expected_tenant_id: UUID, expected_principal_id: UUID) -> None:
    config_dir_value = os.getenv("AZURE_CONFIG_DIR")
    if not config_dir_value:
        raise ValueError("AZURE_CONFIG_DIR must select the isolated product CLI context")
    config_dir = Path(config_dir_value).resolve()
    if not config_dir.is_dir() or stat.S_IMODE(config_dir.stat().st_mode) & 0o077:
        raise ValueError("isolated product CLI context must exist with mode 0700")
    tenant = _az_value(["account", "show", "--query", "tenantId", "--output", "tsv"])
    principal = _az_value(["ad", "signed-in-user", "show", "--query", "id", "--output", "tsv"])
    if tenant.casefold() != str(expected_tenant_id) or principal.casefold() != str(expected_principal_id):
        raise ValueError("active CLI identity does not match the configured document publication principal")


def _az_value(arguments: list[str]) -> str:
    result = subprocess.run(  # noqa: S603
        [AZURE_CLI, *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _canonical_json(value: Mapping[str, object]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def main() -> int:
    arguments = parse_arguments()
    try:
        digest = asyncio.run(publish(arguments))
    except (OSError, subprocess.SubprocessError, ValueError):
        print("FAIL: Document Pack contract publication failed", file=sys.stderr)
        return 1
    print(f"PASS: Document Pack contract published {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
