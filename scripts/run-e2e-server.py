from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID

import uvicorn
from eda_api.auth.models import Principal
from eda_api.auth.repository import InMemoryAuthRepository
from eda_api.config import Settings
from eda_api.main import create_app
from eda_api.storage.uploads import InMemoryBlobStore, InMemoryUploadRepository, UploadService
from eda_api.storage.workspace import InMemoryWorkspaceRepository
from eda_api.task_service import NullTaskEventStore
from eda_runtime_state.tasks import InMemoryRuntimeStateRepository
from fastapi import Request
from fastapi.responses import StreamingResponse
from starlette.routing import Route


class E2EMsalClient:
    async def initiate(self) -> dict[str, object]:
        return {
            "state": "e2e-state-12345678",
            "code_verifier": "e2e-verifier",
            "auth_uri": "https://login.microsoftonline.com/e2e/authorize",
        }

    async def complete(self, flow: dict[str, object], response: dict[str, str]) -> Principal:
        del flow, response
        return Principal(
            tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
            owner_object_id=UUID("22222222-2222-2222-2222-222222222222"),
            audience=UUID("33333333-3333-3333-3333-333333333333"),
        )

    def close(self) -> None:
        return None


def build_app():
    static = Path("apps/web/dist").resolve()
    if not (static / "index.html").is_file():
        raise RuntimeError("frontend E2E bundle is missing; run the web build first")
    settings = Settings.model_validate(
        {
            "app_env": "test",
            "public_origin": "http://127.0.0.1:4173",
            "entra_tenant_id": "11111111-1111-1111-1111-111111111111",
            "entra_client_id": "33333333-3333-3333-3333-333333333333",
            "entra_client_secret": "e2e-only",
            "cosmos_endpoint": "https://e2e.documents.azure.com",
            "blob_account_url": "https://e2e.blob.core.windows.net",
            "cookie_secure": False,
            "redis_url": "redis://127.0.0.1:6379/0",
            "foundry_project_endpoint": "https://e2e.services.ai.azure.com/api/projects/e2e",
            "foundry_model_deployment": "gpt-5.6-terra-medium-v1",
            "frontend_dist": static,
        }
    )
    app = create_app(
        settings_override=settings,
        auth_repository_override=InMemoryAuthRepository(),
        msal_override=E2EMsalClient(),
        workspace_repository_override=InMemoryWorkspaceRepository(),
        upload_service_override=UploadService(
            blob_store=InMemoryBlobStore(),
            upload_repository=InMemoryUploadRepository(),
            upload_limit_bytes=settings.upload_limit_bytes,
        ),
        runtime_repository_override=InMemoryRuntimeStateRepository(),
        event_store_override=NullTaskEventStore(),
    )
    attempts: dict[str, int] = {}

    async def reconnect_fixture(request: Request) -> StreamingResponse:
        task_id = request.path_params["task_id"]
        attempt = attempts.get(task_id, 0) + 1
        attempts[task_id] = attempt
        cursor = request.headers.get("last-event-id")

        async def events() -> AsyncIterator[bytes]:
            if attempt == 1:
                yield (b'retry: 50\nid: 1-0\nevent: analysis_progress\ndata: {"label":"Inspecting workbook"}\n\n')
                return
            if cursor == "1-0":
                yield (
                    b"retry: 10000\nid: 1-0\nevent: analysis_progress\n"
                    b'data: {"label":"Duplicate"}\n\n'
                    b"id: 2-0\nevent: artifact.ready\n"
                    b'data: {"artifactId":"artifact-report"}\n\n'
                    b"id: 3-0\nevent: run.completed\n"
                    b'data: {"finalMessageId":"msg_final"}\n\n'
                )
                return
            yield (b'retry: 10000\nid: 2-0\nevent: analysis_progress\ndata: {"label":"Reconnect cursor missing"}\n\n')

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )

    app.router.routes.insert(
        0,
        Route("/api/tasks/{task_id}/events", reconnect_fixture, methods=["GET"]),
    )
    return app


if __name__ == "__main__":
    uvicorn.run(build_app(), host="127.0.0.1", port=4173, log_level="warning")
