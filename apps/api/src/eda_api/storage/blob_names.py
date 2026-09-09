from __future__ import annotations

import re

from eda_api.auth.models import Principal
from eda_runtime_state.models import TaskPartition

UPLOAD_ID_PATTERN = re.compile(r"^upl_[A-Za-z0-9_-]{8,}$")
SESSION_ID_PATTERN = re.compile(r"^ses_[A-Za-z0-9_-]{16,}$")
ARTIFACT_ID_PATTERN = re.compile(r"^(artifact|input|output|result|script)-[A-Za-z0-9_-]{8,}$")


class BlobNameError(ValueError):
    pass


def quarantine_blob_name(principal: Principal | TaskPartition, upload_id: str) -> str:
    _require(UPLOAD_ID_PATTERN, upload_id, "upload ID")
    return f"quarantine/{principal.tenant_id}/{principal.owner_object_id}/{upload_id}"


def input_blob_name(principal: Principal, session_id: str, artifact_id: str, version: int) -> str:
    _validate_session_artifact_version(session_id, artifact_id, version)
    return f"sessions/{principal.tenant_id}/{principal.owner_object_id}/{session_id}/inputs/{artifact_id}/{version}"


def output_blob_name(principal: Principal, session_id: str, artifact_id: str, version: int) -> str:
    _validate_session_artifact_version(session_id, artifact_id, version)
    return f"sessions/{principal.tenant_id}/{principal.owner_object_id}/{session_id}/outputs/{artifact_id}/{version}"


def _validate_session_artifact_version(session_id: str, artifact_id: str, version: int) -> None:
    _require(SESSION_ID_PATTERN, session_id, "session ID")
    _require(ARTIFACT_ID_PATTERN, artifact_id, "artifact ID")
    if version < 1:
        raise BlobNameError("artifact version must be positive")


def _require(pattern: re.Pattern[str], value: str, label: str) -> None:
    if pattern.fullmatch(value) is None:
        raise BlobNameError(f"invalid {label}")
