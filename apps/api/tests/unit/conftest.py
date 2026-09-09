from __future__ import annotations

from uuid import UUID

import pytest
from eda_api.auth.models import Principal
from eda_api.auth.repository import InMemoryAuthRepository
from eda_api.config import Settings
from eda_api.main import create_app
from eda_api.storage.uploads import InMemoryBlobStore, InMemoryUploadRepository, UploadService
from eda_api.storage.workspace import InMemoryWorkspaceRepository
from eda_api.task_service import NullTaskEventStore
from eda_runtime_state.tasks import InMemoryRuntimeStateRepository
from fastapi.testclient import TestClient


@pytest.fixture
def settings() -> Settings:
    return Settings.model_validate(
        {
            "public_origin": "https://analyst.example.test",
            "entra_tenant_id": "11111111-1111-1111-1111-111111111111",
            "entra_client_id": "33333333-3333-3333-3333-333333333333",
            "entra_client_secret": "local-only",
            "cosmos_endpoint": "https://enterprise-data-analyst.documents.azure.com",
            "blob_account_url": "https://enterprisedataanalyst.blob.core.windows.net",
            "redis_url": "redis://127.0.0.1:6379/0",
            "foundry_project_endpoint": "https://example.services.ai.azure.com/api/projects/example",
            "foundry_model_deployment": "analysis-opus",
        }
    )


class FakeMsalClient:
    async def initiate(self) -> dict[str, object]:
        return {
            "state": "state-12345678",
            "code_verifier": "verifier",
            "auth_uri": "https://login.microsoftonline.com/authorize",
        }

    async def complete(self, flow: dict[str, object], response: dict[str, str]) -> Principal:
        assert flow["state"] == response["state"]
        return Principal(
            tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
            owner_object_id=UUID("22222222-2222-2222-2222-222222222222"),
            audience=UUID("33333333-3333-3333-3333-333333333333"),
        )

    def close(self) -> None:
        return None


@pytest.fixture
def auth_repository() -> InMemoryAuthRepository:
    return InMemoryAuthRepository()


@pytest.fixture
def msal_client() -> FakeMsalClient:
    return FakeMsalClient()


@pytest.fixture
def client(settings: Settings, auth_repository: InMemoryAuthRepository, msal_client: FakeMsalClient) -> TestClient:
    application = create_app(
        settings_override=settings,
        auth_repository_override=auth_repository,
        msal_override=msal_client,
        workspace_repository_override=InMemoryWorkspaceRepository(),
        upload_service_override=UploadService(
            blob_store=InMemoryBlobStore(),
            upload_repository=InMemoryUploadRepository(),
            upload_limit_bytes=settings.upload_limit_bytes,
        ),
        runtime_repository_override=InMemoryRuntimeStateRepository(),
        event_store_override=NullTaskEventStore(),
    )
    with TestClient(
        application,
        base_url=str(settings.public_origin),
        raise_server_exceptions=False,
    ) as test_client:
        yield test_client
