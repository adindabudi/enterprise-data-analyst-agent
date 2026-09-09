from __future__ import annotations

import pytest
from eda_worker.artifacts.provenance import ProvenanceError, TaskManifestBuilder


def test_important_claim_requires_resolvable_evidence() -> None:
    builder = TaskManifestBuilder(task_id="task_12345678")

    with pytest.raises(ProvenanceError):
        builder.add_claim("Revenue grew 15%", important=True, evidence_refs=[])


def test_manifest_excludes_sensitive_runtime_state() -> None:
    builder = TaskManifestBuilder(task_id="task_12345678")
    builder.add_claim("Revenue grew 15%", important=True, evidence_refs=["input-12345678"])

    manifest = builder.build(
        {
            "sessionId": "ds_secret-session",
            "reasoning": "hidden",
            "blobName": "sessions/tenant/owner/private",
            "promptVersion": "prompt-sha",
        }
    )

    assert manifest["taskId"] == "task_12345678"
    assert "sessionId" not in str(manifest)
    assert "reasoning" not in str(manifest)
    assert "blobName" not in str(manifest)
