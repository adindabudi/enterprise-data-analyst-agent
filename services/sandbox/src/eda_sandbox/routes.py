from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated, Literal, Protocol, cast

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from fastapi import Path as ApiPath
from fastapi.responses import StreamingResponse

from .contracts import ExecutionRecord, ExecutionRequest, FileRecord, ValidationRequest, ValidationResult
from .files import FileIndex
from .validation import validate_indexed_file


class ExecutionRunner(Protocol):
    def run(self, request: ExecutionRequest) -> ExecutionRecord: ...


def _file_index(request: Request) -> FileIndex:
    return cast(FileIndex, request.app.state.file_index)


def _execution_manager(request: Request) -> ExecutionRunner:
    return cast(ExecutionRunner, request.app.state.execution_manager)


def _execution_records(request: Request) -> dict[str, ExecutionRecord]:
    return cast(dict[str, ExecutionRecord], request.app.state.execution_records)


FileIndexDependency = Annotated[FileIndex, Depends(_file_index)]
ExecutionManagerDependency = Annotated[ExecutionRunner, Depends(_execution_manager)]
ExecutionRecordsDependency = Annotated[dict[str, ExecutionRecord], Depends(_execution_records)]
ImportCategory = Literal["input", "source"]

router = APIRouter(prefix="/v1", tags=["sandbox"])


@router.post("/files/import", response_model=FileRecord)
async def import_file(
    category: Annotated[ImportCategory, Query()],
    file: Annotated[UploadFile, File()],
    index: FileIndexDependency,
) -> FileRecord:
    return await asyncio.to_thread(
        index.import_stream,
        category,
        file.filename or "unnamed",
        file.file,
    )


@router.get("/files", response_model=list[FileRecord])
def list_files(index: FileIndexDependency) -> list[FileRecord]:
    return list(index.records(("output", "validation")))


@router.get("/files/{file_id}", response_class=StreamingResponse)
def download_file(
    file_id: Annotated[str, ApiPath(pattern=r"^file_[A-Za-z0-9_-]{8,}$")],
    index: FileIndexDependency,
) -> StreamingResponse:
    record = index.record_for(file_id)
    return StreamingResponse(
        _file_chunks(index.path_for(file_id)),
        media_type="application/octet-stream",
        headers={"Content-Length": str(record.size_bytes)},
    )


@router.post("/executions", response_model=ExecutionRecord)
def execute(
    execution: ExecutionRequest,
    manager: ExecutionManagerDependency,
    records: ExecutionRecordsDependency,
) -> ExecutionRecord:
    record = manager.run(execution)
    records[record.execution_id] = record
    return record


@router.get("/executions/{execution_id}", response_model=ExecutionRecord)
def get_execution(
    execution_id: Annotated[str, ApiPath(pattern=r"^exec_[A-Za-z0-9_-]{8,}$")],
    records: ExecutionRecordsDependency,
) -> ExecutionRecord:
    record = records.get(execution_id)
    if record is None:
        from .files import UnsafeFile

        raise UnsafeFile("unknown execution ID")
    return record


@router.post("/validations", response_model=ValidationResult)
def validate_file(validation: ValidationRequest, index: FileIndexDependency) -> ValidationResult:
    return validate_indexed_file(index, validation)


def _file_chunks(path: Path) -> Iterator[bytes]:
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            yield chunk
