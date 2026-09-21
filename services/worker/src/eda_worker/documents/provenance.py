from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from eda_contracts import ArtifactKind, ArtifactRef


@dataclass(frozen=True)
class TrustedDocumentBundle:
    task_id: str
    artifact: ArtifactRef


_DOCUMENT_BUNDLE: ContextVar[TrustedDocumentBundle | None] = ContextVar("trusted_document_bundle", default=None)


@contextmanager
def document_bundle_scope(task_id: str, artifact: ArtifactRef) -> Generator[None]:
    if artifact.kind not in {ArtifactKind.INPUT, ArtifactKind.SCRIPT}:
        raise ValueError("trusted document bundle must be an input or script artifact")
    token = _DOCUMENT_BUNDLE.set(TrustedDocumentBundle(task_id=task_id, artifact=artifact))
    try:
        yield
    finally:
        _DOCUMENT_BUNDLE.reset(token)


def is_trusted_document_bundle(task_id: str, artifact: ArtifactRef) -> bool:
    provenance = _DOCUMENT_BUNDLE.get()
    return provenance is not None and provenance.task_id == task_id and provenance.artifact == artifact
