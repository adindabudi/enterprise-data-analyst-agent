from __future__ import annotations

from datetime import UTC, datetime

import pytest
from azure.cosmos.exceptions import CosmosResourceNotFoundError
from eda_worker.acceptance.documents import DocumentImageContract, document_contract_sha256
from eda_worker.documents.publication import publish_document_contract


class Container:
    def __init__(self) -> None:
        self.items: dict[str, dict[str, object]] = {}

    async def create_item(self, body: dict[str, object]) -> dict[str, object]:
        self.items[str(body["id"])] = dict(body)
        return body

    async def read_item(self, item: str, partition_key: str) -> dict[str, object]:
        assert item == partition_key
        if item not in self.items:
            raise CosmosResourceNotFoundError(message="not found", status_code=404)
        return self.items[item]

    async def replace_item(self, item: str, body: dict[str, object], **kwargs: object) -> dict[str, object]:
        del kwargs
        self.items[item] = dict(body)
        return body


@pytest.mark.asyncio
async def test_publication_writes_immutable_contract_and_configured_feature() -> None:
    contract = DocumentImageContract(
        deploymentId="deployment-1",
        workerImageDigest=f"sha256:{'a' * 64}",
        sandboxImageDigest=f"sha256:{'b' * 64}",
        lockSha256="c" * 64,
        commit="d" * 40,
        bundles={"docx": "1" * 64, "pdf": "2" * 64, "pptx": "3" * 64, "xlsx": "4" * 64},
    )
    container = Container()

    digest = await publish_document_contract(
        container,
        contract,
        now=datetime(2026, 9, 2, tzinfo=UTC),
    )

    assert digest == document_contract_sha256(contract)
    assert container.items[f"feature-contract:documents:{digest}"]["immutable"] is True
    feature = container.items["feature:documents"]
    assert feature["state"] == "configured"
    assert feature["workerImageDigest"] == contract.worker_image_digest
    assert feature["sandboxImageDigest"] == contract.sandbox_image_digest
    assert feature["evidenceSha256"] is None
