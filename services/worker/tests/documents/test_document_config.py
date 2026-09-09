from __future__ import annotations

import hashlib
import json
from pathlib import Path

from eda_worker.documents.config import DocumentSettings
from eda_worker.documents.readiness import document_readiness, load_document_runtime_readiness


def create_skill_root(tmp_path: Path) -> Path:
    root = tmp_path / "skills"
    root.mkdir()
    for name in ("docx", "pdf", "pptx", "xlsx"):
        (root / name).mkdir()
        (root / f"{name}.zip").write_bytes(name.encode())
    return root


def test_disabled_document_pack_never_exposes_ready_state(tmp_path: Path) -> None:
    state = document_readiness(
        DocumentSettings(
            enabled=False, skill_root=tmp_path / "missing", acceptance_evidence_path=tmp_path / "missing.json"
        )
    )

    assert state.status == "disabled"


def test_enabled_pack_is_configured_until_matching_acceptance_evidence_exists(tmp_path: Path) -> None:
    root = create_skill_root(tmp_path)
    state = document_readiness(
        DocumentSettings(enabled=True, skill_root=root, acceptance_evidence_path=tmp_path / "missing.json")
    )

    assert state.status == "configured"


def test_enabled_pack_requires_matching_bundle_hashes_for_ready_state(tmp_path: Path) -> None:
    root = create_skill_root(tmp_path)
    bundles = {
        name: hashlib.sha256((root / f"{name}.zip").read_bytes()).hexdigest()
        for name in ("docx", "pdf", "pptx", "xlsx")
    }
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(json.dumps({"state": "ready", "bundles": bundles}))

    state = document_readiness(DocumentSettings(enabled=True, skill_root=root, acceptance_evidence_path=evidence_path))

    assert state.status == "ready"
    assert state.evidence_digest is not None


class RuntimeContainer:
    def __init__(self, document: dict[str, object] | None) -> None:
        self.document = document

    async def read_item(self, item: str, partition_key: str) -> dict[str, object]:
        assert item == partition_key == "feature:documents"
        if self.document is None:
            raise ValueError("missing")
        return self.document


async def test_runtime_readiness_is_configured_until_matching_feature_is_ready(tmp_path: Path) -> None:
    root = create_skill_root(tmp_path)
    settings = DocumentSettings(enabled=True, skill_root=root)
    bundles = {
        name: hashlib.sha256((root / f"{name}.zip").read_bytes()).hexdigest()
        for name in ("docx", "pdf", "pptx", "xlsx")
    }

    configured = await load_document_runtime_readiness(
        RuntimeContainer(None),
        settings=settings,
        deployment_id="deployment-1",
        worker_image_digest="sha256:" + "9" * 64,
        sandbox_image_digest="sha256:" + "5" * 64,
    )
    ready = await load_document_runtime_readiness(
        RuntimeContainer(
            {
                "id": "feature:documents",
                "state": "ready",
                "deploymentId": "deployment-1",
                "contractSha256": "8" * 64,
                "workerImageDigest": "sha256:" + "9" * 64,
                "sandboxImageDigest": "sha256:" + "5" * 64,
                "lockSha256": "7" * 64,
                "commit": "6" * 40,
                "bundles": bundles,
                "evidenceSha256": "a" * 64,
                "verifiedAt": "2026-07-24T00:00:00Z",
            }
        ),
        settings=settings,
        deployment_id="deployment-1",
        worker_image_digest="sha256:" + "9" * 64,
        sandbox_image_digest="sha256:" + "5" * 64,
    )
    wrong_deployment = await load_document_runtime_readiness(
        RuntimeContainer(
            {
                "id": "feature:documents",
                "state": "ready",
                "deploymentId": "other",
                "contractSha256": "8" * 64,
                "workerImageDigest": "sha256:" + "9" * 64,
                "sandboxImageDigest": "sha256:" + "5" * 64,
                "lockSha256": "7" * 64,
                "commit": "6" * 40,
                "bundles": bundles,
                "evidenceSha256": "a" * 64,
                "verifiedAt": "2026-07-24T00:00:00Z",
            }
        ),
        settings=settings,
        deployment_id="deployment-1",
        worker_image_digest="sha256:" + "9" * 64,
        sandbox_image_digest="sha256:" + "5" * 64,
    )

    assert configured.status == "configured"
    assert ready.status == "ready"
    assert wrong_deployment.status == "failed"
