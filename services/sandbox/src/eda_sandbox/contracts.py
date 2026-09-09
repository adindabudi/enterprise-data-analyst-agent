from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SandboxModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=lambda value: value.split("_")[0] + "".join(part.title() for part in value.split("_")[1:]),
        extra="forbid",
        frozen=True,
        populate_by_name=True,
    )


class ApiProblem(SandboxModel):
    code: str = Field(min_length=1, max_length=80)
    message: str = Field(min_length=1, max_length=200)
    correlation_id: str = Field(pattern=r"^[a-f0-9]{16}$")


class Runtime(StrEnum):
    PYTHON = "python"
    JAVASCRIPT = "javascript"


class ExecutionRequest(SandboxModel):
    runtime: Runtime
    source_file_id: str = Field(pattern=r"^file_[A-Za-z0-9_-]{8,}$")
    parameters_file_id: str | None = Field(default=None, pattern=r"^file_[A-Za-z0-9_-]{8,}$")
    timeout_seconds: int = Field(default=120, ge=1, le=300)


class FileRecord(SandboxModel):
    file_id: str = Field(pattern=r"^file_[A-Za-z0-9_-]{8,}$")
    category: Literal["input", "source", "output", "stdio", "validation"]
    display_name: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class ExecutionStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class ExecutionRecord(SandboxModel):
    execution_id: str
    status: ExecutionStatus
    runtime: Runtime
    source_file_id: str
    stdout_file_id: str | None = None
    stderr_file_id: str | None = None
    output_file_ids: tuple[str, ...] = ()
    return_code: int | None = None
    duration_ms: int = Field(ge=0)
    peak_rss_bytes: int = Field(ge=0)
    cpu_time_ms: int = Field(ge=0)


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


class ValidationStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"


class ValidationRequest(SandboxModel):
    file_id: str = Field(pattern=r"^file_[A-Za-z0-9_-]{8,}$")
    profile: ValidationProfile


class ValidationResult(SandboxModel):
    validation_id: str = Field(pattern=r"^validation_[a-f0-9]{16}$")
    file_id: str
    profile: ValidationProfile
    status: ValidationStatus
    report: FileRecord
