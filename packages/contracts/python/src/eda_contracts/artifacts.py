from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from .base import ContractModel, OpaqueArtifactId, OpaqueSessionId, OpaqueTaskId, Sha256Hex


class ArtifactStatus(StrEnum):
    CREATED = "created"
    GENERATING = "generating"
    VALIDATING = "validating"
    REPAIRING = "repairing"
    READY = "ready"
    REJECTED = "rejected"
    INCOMPLETE = "incomplete"


class ArtifactKind(StrEnum):
    INPUT = "input"
    XLSX = "xlsx"
    XLSM = "xlsm"
    HTML = "html"
    SVG = "svg"
    PNG = "png"
    MERMAID = "mmd"
    PPTX = "pptx"
    DOCX = "docx"
    PDF = "pdf"
    MANIFEST = "manifest"
    SCRIPT = "script"
    DATA = "data"


class ArtifactRef(ContractModel):
    artifact_id: OpaqueArtifactId
    version: int = Field(ge=1)
    kind: ArtifactKind
    sha256: Sha256Hex


class ArtifactRecord(ArtifactRef):
    session_id: OpaqueSessionId
    task_id: OpaqueTaskId
    status: ArtifactStatus
    media_type: str = Field(min_length=3, max_length=127)
    size_bytes: int = Field(ge=0, le=1_073_741_824)
    validation_report_ref: ArtifactRef | None = None
