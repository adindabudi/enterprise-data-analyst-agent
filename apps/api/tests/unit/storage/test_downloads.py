from __future__ import annotations

import pytest
from eda_api.storage.downloads import ArtifactUnavailable, DownloadService
from eda_contracts import ArtifactKind, ArtifactRecord, ArtifactStatus


def artifact(status: ArtifactStatus) -> ArtifactRecord:
    return ArtifactRecord(
        artifact_id="input-12345678",
        version=1,
        kind=ArtifactKind.INPUT,
        sha256="a" * 64,
        session_id="ses_1234567890abcdef",
        task_id="task_12345678",
        status=status,
        media_type="text/csv",
        size_bytes=10,
    )


@pytest.fixture
def download_service() -> DownloadService:
    return DownloadService()


@pytest.mark.parametrize(
    "status",
    [
        ArtifactStatus.CREATED,
        ArtifactStatus.GENERATING,
        ArtifactStatus.VALIDATING,
        ArtifactStatus.REPAIRING,
        ArtifactStatus.REJECTED,
        ArtifactStatus.INCOMPLETE,
    ],
)
def test_non_ready_artifact_has_no_download(status: ArtifactStatus, download_service: DownloadService) -> None:
    with pytest.raises(ArtifactUnavailable):
        download_service.authorize(artifact(status))


def test_ready_artifact_can_be_authorised(download_service: DownloadService) -> None:
    assert download_service.authorize(artifact(ArtifactStatus.READY)).status is ArtifactStatus.READY
