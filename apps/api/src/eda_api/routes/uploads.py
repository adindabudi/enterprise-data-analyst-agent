from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from eda_api.auth.dependencies import CsrfPrincipalDep, CurrentPrincipalDep
from eda_api.dependencies import upload_service, workspace_repository
from eda_api.storage.scans import ScanState
from eda_api.storage.uploads import UploadRejected, UploadService
from eda_api.storage.workspace import WorkspaceRepository
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

router = APIRouter(prefix="/api/sessions", tags=["uploads"])


class UploadInfo(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        serialize_by_alias=True,
    )

    upload_id: str
    display_name: str
    state: ScanState


class UploadStatusInfo(UploadInfo):
    size_bytes: int
    sha256: str


@router.get("/{session_id}/uploads/{upload_id}", response_model=UploadStatusInfo)
async def get_upload_status(
    session_id: str,
    upload_id: str,
    principal: CurrentPrincipalDep,
    workspace: Annotated[WorkspaceRepository, Depends(workspace_repository)],
    service: Annotated[UploadService, Depends(upload_service)],
) -> UploadStatusInfo:
    if await workspace.get_session(principal, session_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    record = await service.get_status(principal, session_id, upload_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return UploadStatusInfo(
        upload_id=record.id,
        display_name=record.display_name,
        state=record.state,
        size_bytes=record.size_bytes,
        sha256=record.sha256,
    )


@router.post("/{session_id}/uploads", response_model=UploadInfo, status_code=status.HTTP_202_ACCEPTED)
async def create_upload(
    session_id: str,
    upload: Annotated[UploadFile, File()],
    principal: CsrfPrincipalDep,
    workspace: Annotated[WorkspaceRepository, Depends(workspace_repository)],
    service: Annotated[UploadService, Depends(upload_service)],
) -> UploadInfo:
    if await workspace.get_session(principal, session_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    try:
        record = await service.create_quarantine_upload(
            principal,
            session_id,
            upload.filename or "upload",
            _file_chunks(upload),
        )
    except UploadRejected as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT) from error
    finally:
        await upload.close()
    return UploadInfo(upload_id=record.id, display_name=record.display_name, state=record.state)


async def _file_chunks(upload: UploadFile) -> AsyncIterator[bytes]:
    while chunk := await upload.read(1024 * 1024):
        yield chunk
