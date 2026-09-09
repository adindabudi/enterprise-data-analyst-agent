from __future__ import annotations

import hashlib
import json

from eda_artifacts.documents import generate_document_corpus, validate_generated_document
from eda_contracts import ArtifactKind
from eda_worker.sandbox.gateway import InMemoryArtifactGatewayStore
from eda_worker.tools.contracts import ValidationProfile

KIND_MAP = {
    "pptx": (ArtifactKind.PPTX, ValidationProfile.DOCUMENT_PPTX),
    "docx": (ArtifactKind.DOCX, ValidationProfile.DOCUMENT_DOCX),
    "xlsx": (ArtifactKind.XLSX, ValidationProfile.DOCUMENT_XLSX),
    "pdf": (ArtifactKind.PDF, ValidationProfile.DOCUMENT_PDF),
}


def renderer(_payload: bytes, page_number: int) -> bytes:
    return f"document-page-{page_number}".encode()


async def test_source_generated_documents_validate_and_publish_through_artifact_store() -> None:
    store = InMemoryArtifactGatewayStore()
    task_id = "task_document_pack_1234"
    published = []

    for document in generate_document_corpus():
        validate_generated_document(document, pdf_renderer=renderer)
        artifact_kind, profile = KIND_MAP[document.kind]
        candidate = await store.persist_bytes(task_id, artifact_kind, document.display_name, document.content)
        validation_payload = json.dumps(
            {
                "profile": profile.value,
                "candidateSha256": candidate.sha256,
                "status": "passed",
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        report = await store.persist_validation(task_id, candidate, profile, validation_payload, "passed")
        published.append(await store.publish(task_id, candidate, report))

    assert {artifact.kind for artifact in published} == {
        ArtifactKind.PPTX,
        ArtifactKind.DOCX,
        ArtifactKind.XLSX,
        ArtifactKind.PDF,
    }
    assert all(artifact.version == 2 for artifact in published)
    assert all(len(artifact.sha256) == 64 for artifact in published)
    assert len({hashlib.sha256(document.content).hexdigest() for document in generate_document_corpus()}) == 4
