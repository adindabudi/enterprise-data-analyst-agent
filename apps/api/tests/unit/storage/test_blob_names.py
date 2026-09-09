from __future__ import annotations

from uuid import UUID

import pytest
from eda_api.auth.models import Principal
from eda_api.storage.blob_names import BlobNameError, input_blob_name, output_blob_name, quarantine_blob_name


@pytest.fixture
def owner() -> Principal:
    return Principal(
        tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
        owner_object_id=UUID("22222222-2222-2222-2222-222222222222"),
        audience=UUID("33333333-3333-3333-3333-333333333333"),
    )


def test_quarantine_name_is_server_generated(owner: Principal) -> None:
    name = quarantine_blob_name(owner, "upl_1234567890")

    assert name == f"quarantine/{owner.tenant_id}/{owner.owner_object_id}/upl_1234567890"
    assert "../" not in name


def test_input_and_output_names_are_owner_and_session_scoped(owner: Principal) -> None:
    session_id = "ses_1234567890abcdef"

    assert input_blob_name(owner, session_id, "input-12345678", 1).endswith(f"{session_id}/inputs/input-12345678/1")
    assert output_blob_name(owner, session_id, "output-12345678", 2).endswith(f"{session_id}/outputs/output-12345678/2")


@pytest.mark.parametrize("upload_id", ["../upl_1234567890", "upl_../escape", "upload-1234567890"])
def test_quarantine_name_rejects_invalid_opaque_identifier(owner: Principal, upload_id: str) -> None:
    with pytest.raises(BlobNameError):
        quarantine_blob_name(owner, upload_id)


@pytest.mark.parametrize("version", [0, -1])
def test_artifact_names_reject_non_positive_versions(owner: Principal, version: int) -> None:
    with pytest.raises(BlobNameError):
        input_blob_name(owner, "ses_1234567890abcdef", "input-12345678", version)
