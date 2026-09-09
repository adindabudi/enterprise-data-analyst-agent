from uuid import UUID

import pytest
from eda_api.auth.models import Principal
from eda_api.storage.models import WorkspaceSession
from eda_api.storage.workspace import InMemoryWorkspaceRepository, StorageConflict

OWNER = Principal(
    tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
    owner_object_id=UUID("22222222-2222-2222-2222-222222222222"),
    audience=UUID("33333333-3333-3333-3333-333333333333"),
)
OTHER = OWNER.model_copy(update={"owner_object_id": UUID("44444444-4444-4444-4444-444444444444")})


@pytest.mark.asyncio
async def test_opaque_id_does_not_bypass_owner_partition() -> None:
    repository = InMemoryWorkspaceRepository()
    created = await repository.create_session(OWNER, "Variance review")

    assert await repository.get_session(OTHER, created.session_id) is None


@pytest.mark.asyncio
async def test_stale_etag_never_overwrites_session() -> None:
    repository = InMemoryWorkspaceRepository()
    created = await repository.create_session(OWNER, "Variance review")
    updated = await repository.rename_session(OWNER, created.session_id, "FY2026 review", created.etag)

    with pytest.raises(StorageConflict):
        await repository.rename_session(OWNER, created.session_id, "Stale title", created.etag)

    assert updated.etag != created.etag


def test_workspace_document_contains_full_hpk() -> None:
    fields = WorkspaceSession.model_json_schema()["required"]

    assert {"tenantId", "ownerObjectId", "sessionId"} <= set(fields)
