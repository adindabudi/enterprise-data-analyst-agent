from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FabricPackStatus(StrEnum):
    DISABLED = "disabled"
    FAILED = "failed"
    CONFIGURED = "configured"
    READY = "ready"


class PowerBiProjectStatus(StrEnum):
    DISABLED = "disabled"


class FabricFeatureState(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, populate_by_name=True)

    id: Literal["feature:fabric-ontology"]
    provider: Literal["ontology"]
    state: Literal["configured", "ready", "failed"]
    provider_contract_sha256: str = Field(alias="providerContractSha256", pattern=r"^[a-f0-9]{64}$")
    deployment_id: str = Field(alias="deploymentId", min_length=1, max_length=128)
    acceptance_evidence_sha256: str | None = Field(
        default=None,
        alias="acceptanceEvidenceSha256",
        pattern=r"^[a-f0-9]{64}$",
    )
    verified_at: datetime = Field(alias="verifiedAt")


class DocumentFeatureState(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, populate_by_name=True)

    id: Literal["feature:documents"] = "feature:documents"
    state: Literal["configured", "ready", "failed"]
    deployment_id: str = Field(alias="deploymentId", min_length=1, max_length=128)
    contract_sha256: str = Field(alias="contractSha256", pattern=r"^[a-f0-9]{64}$")
    evidence_sha256: str | None = Field(default=None, alias="evidenceSha256", pattern=r"^[a-f0-9]{64}$")
    worker_image_digest: str = Field(alias="workerImageDigest", pattern=r"^sha256:[a-f0-9]{64}$")
    sandbox_image_digest: str = Field(alias="sandboxImageDigest", pattern=r"^sha256:[a-f0-9]{64}$")
    lock_sha256: str = Field(alias="lockSha256", pattern=r"^[a-f0-9]{64}$")
    commit: str = Field(pattern=r"^[a-f0-9]{40}$")
    bundles: dict[str, str]
    verified_at: datetime = Field(alias="verifiedAt")

    @model_validator(mode="after")
    def validate_complete(self) -> DocumentFeatureState:
        if set(self.bundles) != {"docx", "pdf", "pptx", "xlsx"} or any(
            len(value) != 64 or any(character not in "0123456789abcdef" for character in value)
            for value in self.bundles.values()
        ):
            raise ValueError("Document Pack bundle hashes are incomplete or malformed")
        if (self.state == "ready") != (self.evidence_sha256 is not None):
            raise ValueError("Document Pack ready state must have exclusive acceptance evidence")
        return self


def fabric_pack_status(*, enabled: bool, feature: FabricFeatureState | None, source_wired: bool) -> FabricPackStatus:
    """A loaded schema snapshot makes the ontology pack `configured`; only acceptance evidence makes it `ready`."""
    if not enabled:
        return FabricPackStatus.DISABLED
    if feature is not None and feature.state == "failed":
        return FabricPackStatus.FAILED
    if feature is not None and feature.state == "ready":
        accepted = feature.acceptance_evidence_sha256 is not None and source_wired
        return FabricPackStatus.READY if accepted else FabricPackStatus.FAILED
    return FabricPackStatus.CONFIGURED if source_wired else FabricPackStatus.FAILED


def document_pack_status(
    *,
    enabled: bool,
    feature: DocumentFeatureState | None,
    deployment_id: str,
    worker_image_digest: str | None,
    sandbox_image_digest: str | None,
) -> FabricPackStatus:
    if not enabled:
        return FabricPackStatus.DISABLED
    if (
        feature is None
        or feature.state == "failed"
        or worker_image_digest is None
        or sandbox_image_digest is None
        or feature.deployment_id != deployment_id
        or feature.worker_image_digest != worker_image_digest
        or feature.sandbox_image_digest != sandbox_image_digest
    ):
        return FabricPackStatus.FAILED
    if feature.state == "ready":
        return FabricPackStatus.READY if feature.evidence_sha256 is not None else FabricPackStatus.FAILED
    return FabricPackStatus.CONFIGURED
