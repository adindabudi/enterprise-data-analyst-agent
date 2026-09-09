from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

DOCUMENT_ACCEPTANCE_TESTS = frozenset({"acquisition", "generation_validation", "no_leak", "concurrency"})
DOCUMENT_KINDS = frozenset({"docx", "pdf", "pptx", "xlsx"})


class DocumentImageContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    deployment_id: str = Field(alias="deploymentId", min_length=1, max_length=128)
    worker_image_digest: str = Field(alias="workerImageDigest", pattern=r"^sha256:[a-f0-9]{64}$")
    sandbox_image_digest: str = Field(alias="sandboxImageDigest", pattern=r"^sha256:[a-f0-9]{64}$")
    lock_sha256: str = Field(alias="lockSha256", pattern=r"^[a-f0-9]{64}$")
    commit: str = Field(pattern=r"^[a-f0-9]{40}$")
    bundles: dict[str, str]

    @model_validator(mode="after")
    def validate_complete(self) -> DocumentImageContract:
        if frozenset(self.bundles) != DOCUMENT_KINDS or any(not _sha(value) for value in self.bundles.values()):
            raise ValueError("document image contract bundle hashes are incomplete or malformed")
        return self


class DocumentAcceptanceObservations(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    state: Literal["passed"] = "passed"
    run_id: str = Field(alias="runId", pattern=r"^run_[A-Za-z0-9_-]{8,}$")
    deployment_id: str = Field(alias="deploymentId", min_length=1, max_length=128)
    contract_sha256: str = Field(alias="contractSha256", pattern=r"^[a-f0-9]{64}$")
    worker_image_digest: str = Field(alias="workerImageDigest", pattern=r"^sha256:[a-f0-9]{64}$")
    sandbox_image_digest: str = Field(alias="sandboxImageDigest", pattern=r"^sha256:[a-f0-9]{64}$")
    lock_sha256: str = Field(alias="lockSha256", pattern=r"^[a-f0-9]{64}$")
    commit: str = Field(pattern=r"^[a-f0-9]{40}$")
    bundles: dict[str, str]
    terms_failure_build_code: Literal["terms_mismatch"] = Field(alias="termsFailureBuildCode")
    first_artifact_sha256: dict[str, str] = Field(alias="firstArtifactSha256")
    second_artifact_sha256: dict[str, str] = Field(alias="secondArtifactSha256")
    validation_report_sha256: dict[str, str] = Field(alias="validationReportSha256")
    preview_sha256: dict[str, str] = Field(alias="previewSha256")
    published_versions: dict[str, Literal[2]] = Field(alias="publishedVersions")
    benchmark_sha256: str = Field(alias="benchmarkSha256", pattern=r"^[a-f0-9]{64}$")
    document_sessions: int = Field(alias="documentSessions", ge=len(DOCUMENT_KINDS), le=len(DOCUMENT_KINDS))
    max_peak_memory_ratio: float = Field(alias="maxPeakMemoryRatio", ge=0, lt=0.8)
    max_duration_seconds: float = Field(alias="maxDurationSeconds", ge=0, le=300)
    oom_count: Literal[0] = Field(alias="oomCount")
    cross_session_leak_count: Literal[0] = Field(alias="crossSessionLeakCount")
    leaked_skill_markers: Literal[0] = Field(alias="leakedSkillMarkers")
    scanned_surfaces: tuple[str, ...] = Field(alias="scannedSurfaces")
    tests: dict[str, Literal["passed", "failed"]]
    observed_at: datetime = Field(alias="observedAt")

    @model_validator(mode="after")
    def validate_complete(self) -> DocumentAcceptanceObservations:
        hash_maps = (
            self.bundles,
            self.first_artifact_sha256,
            self.second_artifact_sha256,
            self.validation_report_sha256,
            self.preview_sha256,
        )
        if any(
            frozenset(values) != DOCUMENT_KINDS or any(not _sha(value) for value in values.values())
            for values in hash_maps
        ):
            raise ValueError("document hash evidence is incomplete or malformed")
        if frozenset(self.published_versions) != DOCUMENT_KINDS:
            raise ValueError("document publication evidence is incomplete")
        if frozenset(self.tests) != DOCUMENT_ACCEPTANCE_TESTS or any(
            value != "passed" for value in self.tests.values()
        ):
            raise ValueError("document acceptance tests are incomplete or failed")
        if any(self.first_artifact_sha256[kind] == self.second_artifact_sha256[kind] for kind in DOCUMENT_KINDS):
            raise ValueError("repeat document generations must have distinct immutable artifact hashes")
        if set(self.scanned_surfaces) != {"application_logs", "app_insights", "cosmos", "blob_manifests"}:
            raise ValueError("document proprietary-content scans are incomplete")
        return self


def document_evidence_sha256(observations: DocumentAcceptanceObservations) -> str:
    payload = json.dumps(
        observations.model_dump(mode="json", by_alias=True),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def document_contract_sha256(contract: DocumentImageContract) -> str:
    payload = json.dumps(
        contract.model_dump(mode="json", by_alias=True),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def _sha(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(character in "0123456789abcdef" for character in value)
