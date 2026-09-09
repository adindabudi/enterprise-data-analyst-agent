from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from eda_contracts import ArtifactRef, ProgressState
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ToolModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ArtifactView(StrEnum):
    METADATA = "metadata"
    SCHEMA = "schema"
    SAMPLE = "sample"


class InspectArtifactOperation(ToolModel):
    artifact: ArtifactRef
    view: ArtifactView
    max_rows: int = Field(default=20, ge=1, le=100)
    max_characters: int = Field(default=8000, ge=100, le=8000)


class SandboxRuntime(StrEnum):
    PYTHON = "python"
    JAVASCRIPT = "javascript"


class ExecuteSandboxOperation(ToolModel):
    runtime: SandboxRuntime
    source: str = Field(min_length=1, max_length=65536)
    input_artifacts: tuple[ArtifactRef, ...] = Field(default=(), max_length=10)
    parameters: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    expected_outputs: tuple[Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,239}$")], ...] = Field(
        default=(), max_length=10,
        description="File names this execution must create directly in outputs/. Empty for calculations without files.",
    )
    timeout_seconds: int = Field(default=120, ge=1, le=300)

    @model_validator(mode="after")
    def bound_parameters(self) -> ExecuteSandboxOperation:
        if len(set(self.expected_outputs)) != len(self.expected_outputs):
            raise ValueError("expected output names must be unique")
        if len(self.parameters) > 32:
            raise ValueError("parameters are limited to 32 entries")
        if any(len(str(key)) > 64 or len(str(value)) > 1000 for key, value in self.parameters.items()):
            raise ValueError("parameter key or value exceeds the bounded tool contract")
        return self


class BuildWebArtifactOperation(ToolModel):
    title: str = Field(min_length=1, max_length=120)
    display_name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,239}\.html$")
    app_source: str = Field(min_length=1, max_length=24_000)
    styles: str = Field(default="", max_length=16_000)


class ValidationProfile(StrEnum):
    CORE_XLSX = "core_xlsx"
    CORE_HTML = "core_html"
    WEB_ARTIFACT_HTML = "web_artifact_html"
    CORE_CHART = "core_chart"
    CORE_MERMAID = "core_mermaid"
    PROVENANCE = "provenance"
    DOCUMENT_PPTX = "document_pptx"
    DOCUMENT_DOCX = "document_docx"
    DOCUMENT_XLSX = "document_xlsx"
    DOCUMENT_PDF = "document_pdf"


class ValidateArtifactOperation(ToolModel):
    artifact: ArtifactRef
    profile: ValidationProfile


class PublishArtifactOperation(ToolModel):
    artifact: ArtifactRef
    validation_report: ArtifactRef


class CapabilityStatus(StrEnum):
    OK = "ok"
    CORRECTABLE_ERROR = "correctable_error"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class CapabilityResult(ToolModel):
    status: CapabilityStatus
    summary: str = Field(max_length=4000)
    artifact_refs: tuple[ArtifactRef, ...] = Field(default=(), max_length=20)
    diagnostic_refs: tuple[ArtifactRef, ...] = Field(default=(), max_length=3)
    provenance_refs: tuple[str, ...] = Field(default=(), max_length=50)
    error_code: str | None = Field(default=None, max_length=80)
    retryable: bool = False


def progress_state_for(status: CapabilityStatus) -> ProgressState:
    """A refused capability returns rather than raising, so the milestone has to read the status."""
    if status in {CapabilityStatus.OK, CapabilityStatus.CANCELLED}:
        return "completed"
    return "failed"
