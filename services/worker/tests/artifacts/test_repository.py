from __future__ import annotations

import pytest
from eda_worker.artifacts.repository import (
    ArtifactCandidate,
    ArtifactStatus,
    InMemoryArtifactRepository,
    PublicationRejected,
    ValidationReport,
    artifact_transition,
)


@pytest.mark.parametrize(
    ("current", "next_status"),
    [
        (ArtifactStatus.CREATED, ArtifactStatus.GENERATING),
        (ArtifactStatus.GENERATING, ArtifactStatus.VALIDATING),
        (ArtifactStatus.VALIDATING, ArtifactStatus.REPAIRING),
        (ArtifactStatus.REPAIRING, ArtifactStatus.VALIDATING),
        (ArtifactStatus.VALIDATING, ArtifactStatus.READY),
    ],
)
def test_allowed_artifact_transitions(current: ArtifactStatus, next_status: ArtifactStatus) -> None:
    assert artifact_transition(current, next_status) is next_status


def candidate() -> ArtifactCandidate:
    return ArtifactCandidate(
        artifact_id="output-12345678",
        version=1,
        content_hash="a" * 64,
        status=ArtifactStatus.VALIDATING,
    )


def passing_report() -> ValidationReport:
    return ValidationReport(status="passed", artifact_id="output-12345678", content_hash="a" * 64, profile="core")


def test_ready_requires_passing_report() -> None:
    repository = InMemoryArtifactRepository()
    failed = ValidationReport(status="failed", artifact_id="output-12345678", content_hash="a" * 64, profile="core")

    with pytest.raises(PublicationRejected):
        repository.publish(candidate(), failed, "operation-1")


def test_publication_retry_returns_same_version() -> None:
    repository = InMemoryArtifactRepository()

    first = repository.publish(candidate(), passing_report(), "operation-1")
    second = repository.publish(candidate(), passing_report(), "operation-1")

    assert first == second
    assert first.version == 1


def test_artifact_is_resolvable_only_after_ready_publication() -> None:
    repository = InMemoryArtifactRepository()

    assert repository.resolve_ready("operation-1") is None
    ready = repository.publish(candidate(), passing_report(), "operation-1")

    assert repository.resolve_ready("operation-1") == ready
