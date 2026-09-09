from collections.abc import AsyncIterator

import pytest
from asgi_lifespan import LifespanManager
from eda_api.config import Settings
from eda_api.main import create_app
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _stub_frontend_dist(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory) -> None:
    # The SPA bundle (apps/api/static) is a gitignored build artifact that CI does not
    # produce, so provide a minimal stand-in dist for the frontend-serving routes.
    dist = tmp_path_factory.mktemp("frontend-dist")
    (dist / "index.html").write_text(
        '<!doctype html>\n<html>\n  <body>\n    <div id="root"></div>\n  </body>\n</html>\n'
    )
    monkeypatch.setenv("EDA_FRONTEND_DIST", str(dist))


@pytest.fixture
def test_settings() -> Settings:
    return Settings.model_validate(
        {
            "public_origin": "http://localhost:8000",
            "entra_tenant_id": "11111111-1111-1111-1111-111111111111",
            "entra_client_id": "22222222-2222-2222-2222-222222222222",
            "entra_client_secret": "local-only",
            "cosmos_endpoint": "https://enterprise-data-analyst.documents.azure.com",
            "blob_account_url": "https://enterprisedataanalyst.blob.core.windows.net",
            "redis_url": "redis://127.0.0.1:6379/0",
            "foundry_project_endpoint": "https://example.services.ai.azure.com/api/projects/example",
            "foundry_model_deployment": "analysis-opus",
        }
    )


@pytest.fixture
def api_app(test_settings: Settings):
    from eda_api.auth.repository import InMemoryAuthRepository
    from eda_api.storage.uploads import InMemoryBlobStore, InMemoryUploadRepository, UploadService
    from eda_api.storage.workspace import InMemoryWorkspaceRepository
    from eda_api.task_service import NullTaskEventStore
    from eda_runtime_state.tasks import InMemoryRuntimeStateRepository

    return create_app(
        settings_override=test_settings,
        auth_repository_override=InMemoryAuthRepository(),
        workspace_repository_override=InMemoryWorkspaceRepository(),
        upload_service_override=UploadService(
            blob_store=InMemoryBlobStore(),
            upload_repository=InMemoryUploadRepository(),
            upload_limit_bytes=test_settings.upload_limit_bytes,
        ),
        runtime_repository_override=InMemoryRuntimeStateRepository(),
        event_store_override=NullTaskEventStore(),
    )


@pytest.fixture
async def api_client(api_app) -> AsyncIterator[AsyncClient]:
    async with LifespanManager(api_app):
        async with AsyncClient(transport=ASGITransport(app=api_app), base_url="http://test") as client:
            yield client
    assert api_app.state.started is False
