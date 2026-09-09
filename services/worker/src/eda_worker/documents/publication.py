from __future__ import annotations

import hmac
import json
from datetime import UTC, datetime
from typing import Any, Protocol, cast

from azure.core import MatchConditions
from azure.cosmos.exceptions import CosmosResourceExistsError, CosmosResourceNotFoundError

from eda_worker.acceptance.documents import DocumentImageContract, document_contract_sha256


class DocumentContractContainer(Protocol):
    async def create_item(self, body: dict[str, object]) -> object: ...

    async def read_item(self, item: str, *, partition_key: str) -> object: ...

    async def replace_item(self, item: str, body: dict[str, object], **kwargs: object) -> object: ...


async def publish_document_contract(
    container: DocumentContractContainer,
    contract: DocumentImageContract,
    *,
    now: datetime | None = None,
) -> str:
    contract_digest = document_contract_sha256(contract)
    contract_payload = contract.model_dump(mode="json", by_alias=True)
    timestamp = (now or datetime.now(UTC)).isoformat()
    immutable_id = f"feature-contract:documents:{contract_digest}"
    immutable_document: dict[str, object] = {
        "id": immutable_id,
        "recordType": "featureContract",
        "feature": "documents",
        "immutable": True,
        "contractSha256": contract_digest,
        "contract": contract_payload,
        "createdAt": timestamp,
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
        "verifiedAt": timestamp,
    }
    await _create_immutable(container, immutable_document)
    await _replace_feature(container, feature_document)
    return contract_digest


async def _create_immutable(container: DocumentContractContainer, document: dict[str, object]) -> None:
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


async def _replace_feature(container: DocumentContractContainer, document: dict[str, object]) -> None:
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
        identifier,
        document,
        etag=etag,
        match_condition=MatchConditions.IfNotModified,
    )


def _canonical_json(value: dict[str, object]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
