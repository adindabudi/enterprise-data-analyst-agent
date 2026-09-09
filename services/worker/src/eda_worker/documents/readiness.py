from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal, Protocol

from agent_framework import SkillsProvider
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .config import DocumentSettings

SKILL_NAMES = ("docx", "pdf", "pptx", "xlsx")


class DocumentReadinessStatus(StrEnum):
    DISABLED = "disabled"
    CONFIGURED = "configured"
    READY = "ready"
    FAILED = "failed"


class DocumentAcceptanceEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state: str = Field(pattern=r"^ready$")
    bundles: dict[str, str]


class DocumentFeatureRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    id: Literal["feature:documents"] = "feature:documents"
    record_type: Literal["featureState"] = Field(default="featureState", alias="recordType")
    state: Literal["configured", "ready", "failed"]
    deployment_id: str = Field(alias="deploymentId", min_length=1, max_length=128)
    contract_sha256: str = Field(alias="contractSha256", pattern=r"^[a-f0-9]{64}$")
    worker_image_digest: str = Field(alias="workerImageDigest", pattern=r"^sha256:[a-f0-9]{64}$")
    sandbox_image_digest: str = Field(alias="sandboxImageDigest", pattern=r"^sha256:[a-f0-9]{64}$")
    lock_sha256: str = Field(alias="lockSha256", pattern=r"^[a-f0-9]{64}$")
    commit: str = Field(pattern=r"^[a-f0-9]{40}$")
    bundles: dict[str, str]
    evidence_sha256: str | None = Field(default=None, alias="evidenceSha256", pattern=r"^[a-f0-9]{64}$")
    verified_at: datetime = Field(alias="verifiedAt")

    @model_validator(mode="after")
    def validate_complete(self) -> DocumentFeatureRecord:
        if set(self.bundles) != set(SKILL_NAMES) or any(
            len(value) != 64 or any(character not in "0123456789abcdef" for character in value)
            for value in self.bundles.values()
        ):
            raise ValueError("Document Pack bundle hashes are incomplete or malformed")
        if (self.state == "ready") != (self.evidence_sha256 is not None):
            raise ValueError("Document Pack ready state must have exclusive acceptance evidence")
        return self


class DocumentRuntimeContainer(Protocol):
    async def read_item(self, item: str, partition_key: str) -> dict[str, object]: ...


@dataclass(frozen=True)
class DocumentReadiness:
    status: DocumentReadinessStatus
    evidence_digest: str | None = None


def document_readiness(settings: DocumentSettings) -> DocumentReadiness:
    if not settings.enabled:
        return DocumentReadiness(status=DocumentReadinessStatus.DISABLED)
    if not skill_tree_is_complete(settings.skill_root):
        return DocumentReadiness(status=DocumentReadinessStatus.FAILED)
    if not settings.acceptance_evidence_path.is_file():
        return DocumentReadiness(status=DocumentReadinessStatus.CONFIGURED)
    try:
        raw_evidence = settings.acceptance_evidence_path.read_bytes()
        evidence = DocumentAcceptanceEvidence.model_validate_json(raw_evidence)
        if set(evidence.bundles) != set(SKILL_NAMES):
            raise ValueError("document acceptance evidence must list all bundles")
        for name in SKILL_NAMES:
            if evidence.bundles[name] != sha256_file(settings.skill_root / f"{name}.zip"):
                raise ValueError("document acceptance evidence bundle hash does not match")
    except (OSError, ValueError, json.JSONDecodeError):
        return DocumentReadiness(status=DocumentReadinessStatus.FAILED)
    return DocumentReadiness(
        status=DocumentReadinessStatus.READY,
        evidence_digest=hashlib.sha256(raw_evidence).hexdigest(),
    )


async def load_document_runtime_readiness(
    container: DocumentRuntimeContainer,
    *,
    settings: DocumentSettings,
    deployment_id: str,
    worker_image_digest: str | None,
    sandbox_image_digest: str | None,
) -> DocumentReadiness:
    if not settings.enabled:
        return DocumentReadiness(status=DocumentReadinessStatus.DISABLED)
    if not skill_tree_is_complete(settings.skill_root):
        return DocumentReadiness(status=DocumentReadinessStatus.FAILED)
    if worker_image_digest is None or sandbox_image_digest is None:
        return DocumentReadiness(status=DocumentReadinessStatus.FAILED)
    local_bundles = {name: sha256_file(settings.skill_root / f"{name}.zip") for name in SKILL_NAMES}
    try:
        raw = await container.read_item("feature:documents", partition_key="feature:documents")
    except Exception:
        # An unwritten record and an unreachable store both land here but mean different things,
        # and only this line separates them once the pack is quietly missing in production.
        logging.getLogger(__name__).exception("document feature record could not be read")
        return DocumentReadiness(status=DocumentReadinessStatus.CONFIGURED)
    try:
        feature = DocumentFeatureRecord.model_validate(
            {key: value for key, value in raw.items() if not key.startswith("_")}
        )
    except ValueError:
        return DocumentReadiness(status=DocumentReadinessStatus.FAILED)
    if (
        feature.state == "failed"
        or feature.deployment_id != deployment_id
        or feature.worker_image_digest != worker_image_digest
        or feature.sandbox_image_digest != sandbox_image_digest
        or feature.bundles != local_bundles
    ):
        return DocumentReadiness(status=DocumentReadinessStatus.FAILED)
    if feature.state == "ready" and feature.evidence_sha256 is not None:
        return DocumentReadiness(
            status=DocumentReadinessStatus.READY,
            evidence_digest=feature.evidence_sha256,
        )
    return DocumentReadiness(status=DocumentReadinessStatus.CONFIGURED)


def ready_skills_provider(
    readiness: DocumentReadiness | None,
    provider: SkillsProvider | None,
) -> SkillsProvider | None:
    if readiness is None or readiness.status in {
        DocumentReadinessStatus.DISABLED,
        DocumentReadinessStatus.CONFIGURED,
        DocumentReadinessStatus.FAILED,
    }:
        return None
    if provider is None:
        raise RuntimeError("document skills are ready but no skills provider was supplied")
    return provider


def skill_tree_is_complete(skill_root: Path) -> bool:
    return all((skill_root / name).is_dir() and (skill_root / f"{name}.zip").is_file() for name in SKILL_NAMES)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
