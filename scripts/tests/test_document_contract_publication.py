from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/publish-document-contract.py"


def load_module() -> Any:
    specification = importlib.util.spec_from_file_location("publish_document_contract", SCRIPT)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


class FeatureContainer:
    def __init__(self, existing: dict[str, object]) -> None:
        self.existing = existing
        self.replacements: list[dict[str, object]] = []

    async def read_item(self, identifier: str, partition_key: str) -> dict[str, object]:
        assert identifier == partition_key == "feature:documents"
        return self.existing

    async def replace_item(self, **kwargs: object) -> None:
        self.replacements.append(kwargs)


async def test_identical_ready_feature_is_not_downgraded() -> None:
    module = load_module()
    container = FeatureContainer(
        {"id": "feature:documents", "state": "ready", "contractSha256": "a" * 64, "_etag": "etag"}
    )

    await module._replace_feature(
        container,
        {"id": "feature:documents", "state": "configured", "contractSha256": "a" * 64},
    )

    assert container.replacements == []


async def test_changed_contract_uses_etag_replace() -> None:
    module = load_module()
    container = FeatureContainer(
        {"id": "feature:documents", "state": "ready", "contractSha256": "a" * 64, "_etag": "etag"}
    )

    await module._replace_feature(
        container,
        {"id": "feature:documents", "state": "configured", "contractSha256": "b" * 64},
    )

    assert len(container.replacements) == 1
    assert container.replacements[0]["etag"] == "etag"
    assert container.replacements[0]["item"] == "feature:documents"


def test_publisher_is_principal_bound_and_never_upserts() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "AZURE_CONFIG_DIR" in source
    assert "expected_principal_id" in source
    assert "MatchConditions.IfNotModified" in source
    assert "feature-contract:documents:" in source
    assert "upsert_item" not in source
