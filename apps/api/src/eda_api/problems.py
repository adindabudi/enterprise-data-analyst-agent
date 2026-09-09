from __future__ import annotations

import logging
import secrets
from collections.abc import Awaitable, Callable

from eda_contracts import ApiProblem
from eda_fabric_auth.repository import FabricGrantConflict
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response

from eda_api.auth.msal_client import AuthenticationError

logger = logging.getLogger(__name__)


def _record_failure(request: Request, exception: BaseException) -> None:
    logger.error(
        "unhandled request failure: %s %s",
        request.method,
        request.url.path,
        exc_info=exception,
        extra={"correlation_id": getattr(request.state, "correlation_id", None)},
    )


class ApiError(Exception):
    def __init__(self, *, status_code: int, title: str, code: str) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.title = title
        self.code = code


def problem_response(
    request: Request,
    *,
    status_code: int,
    title: str,
    code: str,
) -> JSONResponse:
    problem = ApiProblem(
        title=title,
        status=status_code,
        code=code,
        correlation_id=request.state.correlation_id,
    )
    return JSONResponse(status_code=status_code, content=problem.model_dump(mode="json", by_alias=True))


async def correlation_id_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    request.state.correlation_id = f"corr_{secrets.token_urlsafe(12)}"
    try:
        response = await call_next(request)
    except Exception as exception:
        _record_failure(request, exception)
        response = problem_response(
            request,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            title="Internal server error",
            code="internal_error",
        )
    response.headers["X-Correlation-ID"] = request.state.correlation_id
    return response


async def authentication_error_handler(request: Request, _: Exception) -> JSONResponse:
    return problem_response(
        request,
        status_code=status.HTTP_401_UNAUTHORIZED,
        title="Authentication failed",
        code="authentication_failed",
    )


async def api_error_handler(request: Request, exception: Exception) -> JSONResponse:
    assert isinstance(exception, ApiError)
    return problem_response(
        request,
        status_code=exception.status_code,
        title=exception.title,
        code=exception.code,
    )


async def http_exception_handler(request: Request, exception: Exception) -> JSONResponse:
    status_code = (
        exception.status_code if isinstance(exception, HTTPException) else status.HTTP_500_INTERNAL_SERVER_ERROR
    )
    if status_code == status.HTTP_401_UNAUTHORIZED:
        title, code = "Authentication required", "authentication_required"
    elif status_code == status.HTTP_403_FORBIDDEN:
        title, code = "Forbidden", "forbidden"
    elif status_code == status.HTTP_404_NOT_FOUND:
        title, code = "Not found", "not_found"
    else:
        title, code = "Request failed", "request_failed"
    return problem_response(request, status_code=status_code, title=title, code=code)


async def validation_exception_handler(request: Request, _: Exception) -> JSONResponse:
    return problem_response(
        request,
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        title="Invalid request",
        code="invalid_request",
    )


async def unhandled_exception_handler(request: Request, exception: Exception) -> JSONResponse:
    _record_failure(request, exception)
    return problem_response(
        request,
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        title="Internal server error",
        code="internal_error",
    )


async def fabric_grant_conflict_handler(request: Request, _: Exception) -> JSONResponse:
    return problem_response(
        request,
        status_code=status.HTTP_409_CONFLICT,
        title="Fabric account already linked",
        code="fabric_grant_conflict",
    )


def install_problem_handlers(app: FastAPI) -> None:
    app.middleware("http")(correlation_id_middleware)
    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(AuthenticationError, authentication_error_handler)
    app.add_exception_handler(FabricGrantConflict, fabric_grant_conflict_handler)
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
