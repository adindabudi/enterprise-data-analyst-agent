from __future__ import annotations

import secrets
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from .contracts import ApiProblem
from .executor import ExecutionManager
from .files import FileIndex, FileLimitExceeded, UnsafeFile
from .routes import ExecutionRunner, router


class Health(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: str


def create_app(
    *,
    file_index: FileIndex | None = None,
    execution_manager: ExecutionRunner | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncGenerator[None]:
        index = file_index or FileIndex()
        manager = execution_manager or ExecutionManager(index.path_for, file_index=index)
        application.state.file_index = index
        application.state.execution_manager = manager
        application.state.execution_records = {}
        try:
            yield
        finally:
            index.clear()

    app = FastAPI(title="Enterprise Data Analyst Sandbox", version="0.1.0", lifespan=lifespan)

    async def liveness() -> Health:
        return Health(status="alive")

    async def readiness() -> Health:
        return Health(status="ready")

    app.add_api_route("/health/live", liveness, methods=["GET"], response_model=Health, include_in_schema=False)
    app.add_api_route("/health/ready", readiness, methods=["GET"], response_model=Health, include_in_schema=False)
    app.include_router(router)

    async def file_error_handler(request: Request, error: FileLimitExceeded | UnsafeFile) -> JSONResponse:
        del request
        status_code = 413 if isinstance(error, FileLimitExceeded) else 404
        problem = ApiProblem(
            code="file_limit_exceeded" if isinstance(error, FileLimitExceeded) else "resource_not_found",
            message="The sandbox request could not be completed.",
            correlation_id=secrets.token_hex(8),
        )
        return JSONResponse(status_code=status_code, content=problem.model_dump(mode="json", by_alias=True))

    async def validation_error_handler(request: Request, error: RequestValidationError) -> JSONResponse:
        del request, error
        problem = ApiProblem(
            code="invalid_request",
            message="The sandbox request is invalid.",
            correlation_id=secrets.token_hex(8),
        )
        return JSONResponse(status_code=422, content=problem.model_dump(mode="json", by_alias=True))

    app.add_exception_handler(FileLimitExceeded, file_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(UnsafeFile, file_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_error_handler)  # type: ignore[arg-type]
    return app


app = create_app()
