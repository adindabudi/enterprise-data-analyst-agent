from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ArtifactStatus(StrEnum):
    CREATED = "created"
    GENERATING = "generating"
    VALIDATING = "validating"
    REPAIRING = "repairing"
    READY = "ready"
    REJECTED = "rejected"
    INCOMPLETE = "incomplete"


class PublicationRejected(ValueError):
    pass


@dataclass(frozen=True)
class ArtifactCandidate:
    artifact_id: str
    version: int
    content_hash: str
    status: ArtifactStatus


@dataclass(frozen=True)
class ValidationReport:
    status: str
    artifact_id: str
    content_hash: str
    profile: str


ALLOWED: dict[ArtifactStatus, set[ArtifactStatus]] = {
    ArtifactStatus.CREATED: {ArtifactStatus.GENERATING},
    ArtifactStatus.GENERATING: {ArtifactStatus.VALIDATING, ArtifactStatus.INCOMPLETE},
    ArtifactStatus.VALIDATING: {
        ArtifactStatus.REPAIRING,
        ArtifactStatus.READY,
        ArtifactStatus.REJECTED,
        ArtifactStatus.INCOMPLETE,
    },
    ArtifactStatus.REPAIRING: {ArtifactStatus.VALIDATING},
}


def artifact_transition(current: ArtifactStatus, next_status: ArtifactStatus) -> ArtifactStatus:
    if next_status not in ALLOWED.get(current, set()):
        raise PublicationRejected(f"invalid artifact transition: {current.value} -> {next_status.value}")
    return next_status


class InMemoryArtifactRepository:
    def __init__(self) -> None:
        self._published: dict[str, ArtifactCandidate] = {}

    def resolve_ready(self, operation_key: str) -> ArtifactCandidate | None:
        candidate = self._published.get(operation_key)
        return candidate if candidate is not None and candidate.status is ArtifactStatus.READY else None

    def publish(self, candidate: ArtifactCandidate, report: ValidationReport, operation_key: str) -> ArtifactCandidate:
        existing = self._published.get(operation_key)
        if existing is not None:
            return existing
        if candidate.status is not ArtifactStatus.VALIDATING:
            raise PublicationRejected("candidate is not validating")
        if (
            report.status != "passed"
            or report.artifact_id != candidate.artifact_id
            or report.content_hash != candidate.content_hash
        ):
            raise PublicationRejected("validation report does not bind to candidate")
        ready = ArtifactCandidate(
            artifact_id=candidate.artifact_id,
            version=candidate.version,
            content_hash=candidate.content_hash,
            status=artifact_transition(candidate.status, ArtifactStatus.READY),
        )
        self._published[operation_key] = ready
        return ready
