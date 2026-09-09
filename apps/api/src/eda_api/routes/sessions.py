from __future__ import annotations

from typing import Annotated

from eda_api.auth.dependencies import CsrfPrincipalDep, CurrentPrincipalDep
from eda_api.dependencies import session_history_reader, session_todo_reader, workspace_repository
from eda_api.problems import ApiError
from eda_api.storage.history import SessionHistory, SessionHistoryReader
from eda_api.storage.models import WorkspaceSession
from eda_api.storage.todos import SessionTodoReader, TodoItemView
from eda_api.storage.workspace import StorageConflict, WorkspaceRepository
from eda_contracts import SessionSummary
from fastapi import APIRouter, Depends, Header, HTTPException, Path, Response, status
from pydantic import BaseModel, ConfigDict, Field

router = APIRouter(prefix="/api/sessions", tags=["sessions"])
SessionIdPath = Annotated[str, Path(pattern=r"^ses_[A-Za-z0-9_-]{16,}$")]


class CreateSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str = Field(min_length=1, max_length=120)


class RenameSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str = Field(min_length=1, max_length=120)


class SessionTodoList(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[TodoItemView, ...] = ()


def public_session(session: WorkspaceSession) -> SessionSummary:
    return SessionSummary(
        session_id=session.session_id,
        title=session.title,
        last_activity_at=session.last_activity_at,
    )


@router.post("", response_model=SessionSummary, status_code=status.HTTP_201_CREATED)
async def create_session(
    request: CreateSessionRequest,
    response: Response,
    principal: CsrfPrincipalDep,
    repository: Annotated[WorkspaceRepository, Depends(workspace_repository)],
) -> SessionSummary:
    session = await repository.create_session(principal, request.title)
    response.headers["ETag"] = session.etag
    return public_session(session)


@router.get("", response_model=list[SessionSummary])
async def list_sessions(
    principal: CurrentPrincipalDep,
    repository: Annotated[WorkspaceRepository, Depends(workspace_repository)],
) -> list[SessionSummary]:
    return [public_session(session) for session in await repository.list_sessions(principal)]


@router.get("/{session_id}", response_model=SessionSummary)
async def get_session(
    session_id: SessionIdPath,
    response: Response,
    principal: CurrentPrincipalDep,
    repository: Annotated[WorkspaceRepository, Depends(workspace_repository)],
) -> SessionSummary:
    session = await repository.get_session(principal, session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    response.headers["ETag"] = session.etag
    return public_session(session)


@router.get("/{session_id}/todos", response_model=SessionTodoList)
async def list_session_todos(
    session_id: SessionIdPath,
    principal: CurrentPrincipalDep,
    repository: Annotated[WorkspaceRepository, Depends(workspace_repository)],
    reader: Annotated[SessionTodoReader | None, Depends(session_todo_reader)],
) -> SessionTodoList:
    if await repository.get_session(principal, session_id) is None or reader is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return SessionTodoList(items=await reader.list_todos(principal, session_id))


@router.get("/{session_id}/history", response_model=SessionHistory)
async def read_session_history(
    session_id: SessionIdPath,
    principal: CurrentPrincipalDep,
    repository: Annotated[WorkspaceRepository, Depends(workspace_repository)],
    reader: Annotated[SessionHistoryReader | None, Depends(session_history_reader)],
) -> SessionHistory:
    if await repository.get_session(principal, session_id) is None:
        raise HTTPException(status_code=404)
    if reader is None:
        raise HTTPException(status_code=503, detail="Session history is unavailable")
    return await reader.read_history(principal, session_id)


@router.patch("/{session_id}", response_model=SessionSummary)
async def rename_session(
    session_id: SessionIdPath,
    request: RenameSessionRequest,
    response: Response,
    principal: CsrfPrincipalDep,
    repository: Annotated[WorkspaceRepository, Depends(workspace_repository)],
    expected_etag: Annotated[str, Header(alias="If-Match")],
) -> SessionSummary:
    if await repository.get_session(principal, session_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    try:
        session = await repository.rename_session(principal, session_id, request.title, expected_etag)
    except StorageConflict as error:
        raise ApiError(
            status_code=status.HTTP_409_CONFLICT,
            title="Session update conflict",
            code="etag_conflict",
        ) from error
    response.headers["ETag"] = session.etag
    return public_session(session)


@router.delete("/{session_id}", status_code=status.HTTP_202_ACCEPTED)
async def request_session_deletion(
    session_id: SessionIdPath,
    principal: CsrfPrincipalDep,
    repository: Annotated[WorkspaceRepository, Depends(workspace_repository)],
) -> None:
    if await repository.get_session(principal, session_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
