from __future__ import annotations

from eda_contracts import ArtifactRecord, ArtifactStatus


class ArtifactUnavailable(ValueError):
    pass


class DownloadService:
    def authorize(self, artifact: ArtifactRecord) -> ArtifactRecord:
        if artifact.status is not ArtifactStatus.READY:
            raise ArtifactUnavailable("artifact is not ready")
        return artifact
