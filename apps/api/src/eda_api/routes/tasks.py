from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Literal

from eda_api.auth.dependencies import CsrfPrincipalDep, CurrentPrincipalDep
from eda_api.dependencies import artifact_catalog, task_service
from eda_api.storage.artifacts import ArtifactCatalog, PublishedArtifact
from eda_api.task_service import TaskService, TaskSummaryView
from eda_contracts import SteeringRequest
from eda_contracts.tasks import TaskStatus
from eda_runtime_state.models import TaskRecord
from fastapi import APIRouter, Depends, Header, HTTPException, Path, Request
from fastapi.responses import Response
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

router = APIRouter(prefix="/api/tasks", tags=["tasks"])
_REDIS_CURSOR = re.compile(r"^\d+-\d+$")
_TERMINAL_EVENTS = {"run.completed", "run.failed", "run.cancelled"}


async def owned_task(
    task_id: str,
    principal: CurrentPrincipalDep,
    service: Annotated[TaskService, Depends(task_service)],
) -> TaskRecord:
    task = await service.get_owned_task(principal, task_id)
    if task is None:
        raise HTTPException(status_code=404)
    return task


OwnedTaskDep = Annotated[TaskRecord, Depends(owned_task)]
TaskServiceDep = Annotated[TaskService, Depends(task_service)]
IdempotencyKeyHeader = Annotated[str, Header(alias="Idempotency-Key", pattern=r"^[!-~]{8,128}$")]


async def owned_task_for_write(
    task_id: str,
    principal: CsrfPrincipalDep,
    service: Annotated[TaskService, Depends(task_service)],
) -> TaskRecord:
    task = await service.get_owned_task(principal, task_id)
    if task is None:
        raise HTTPException(status_code=404)
    return task


OwnedTaskWriteDep = Annotated[TaskRecord, Depends(owned_task_for_write)]


class TaskControlAccepted(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        serialize_by_alias=True,
    )

    command_id: str = Field(pattern=r"^cmd_[A-Za-z0-9_-]{8,}$")
    sequence: int = Field(ge=1)


class CancellationAccepted(TaskControlAccepted):
    cancellation_requested: Literal[True] = True


class FinalMessageResponse(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        serialize_by_alias=True,
    )

    message_id: str = Field(pattern=r"^msg_[A-Za-z0-9_-]{8,}$")
    role: Literal["assistant"] = "assistant"
    text: str = Field(min_length=1, max_length=1000000)


class PublishedArtifactList(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        serialize_by_alias=True,
    )

    artifacts: tuple[PublishedArtifact, ...] = ()


class SourceQueryView(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        serialize_by_alias=True,
    )

    artifact_id: str
    version: int
    sha256: str
    display_name: str
    query: str
    query_sha256: str
    row_count: int
    source_alias: str | None
    message_id: str | None
    executed_at: datetime


class TaskProvenance(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        serialize_by_alias=True,
    )

    source_queries: tuple[SourceQueryView, ...] = ()
    artifacts: tuple[PublishedArtifact, ...] = ()


ArtifactCatalogDep = Annotated[ArtifactCatalog | None, Depends(artifact_catalog)]


def _required_catalog(catalog: ArtifactCatalog | None) -> ArtifactCatalog:
    if catalog is None:
        raise HTTPException(status_code=404)
    return catalog


@router.get("/{task_id}/artifacts", response_model=PublishedArtifactList)
async def list_task_artifacts(task: OwnedTaskDep, catalog: ArtifactCatalogDep) -> PublishedArtifactList:
    reader = _required_catalog(catalog)
    inputs = await reader.list_inputs(task) if task.input_artifacts else ()
    return PublishedArtifactList(artifacts=(*inputs, *await reader.list_published(task)))


@router.get("/{task_id}/provenance", response_model=TaskProvenance)
async def read_task_provenance(task: OwnedTaskDep, catalog: ArtifactCatalogDep) -> TaskProvenance:
    """What the analysis read, and what it published. An artifact alone proves nothing."""
    return TaskProvenance(
        source_queries=tuple(
            SourceQueryView(
                artifact_id=stored.artifact_id,
                version=stored.version,
                sha256=stored.sha256,
                display_name=stored.display_name,
                query=stored.query,
                query_sha256=stored.query_sha256,
                row_count=stored.row_count,
                source_alias=stored.source_alias,
                message_id=stored.message_id,
                executed_at=stored.executed_at,
            )
            for stored in task.query_results
        ),
        artifacts=await _required_catalog(catalog).list_published(task),
    )


@router.get("/{task_id}/artifacts/{artifact_id}/versions/{version}/content")
async def download_task_artifact(
    artifact_id: Annotated[str, Path(pattern=r"^artifact-[A-Za-z0-9_-]{8,}$")],
    version: Annotated[int, Path(ge=1, le=1000)],
    task: OwnedTaskDep,
    catalog: ArtifactCatalogDep,
) -> Response:
    try:
        artifact, content = await _required_catalog(catalog).read_content(task, artifact_id, version)
    except LookupError:
        raise HTTPException(status_code=404) from None
    # Artifacts are model-generated, so they are never served inline on the app origin.
    return Response(
        content=content,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{_safe_filename(artifact.display_name)}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


def _safe_filename(display_name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", display_name).lstrip(".")
    return cleaned[:120] or "artifact.bin"


@router.get("/{task_id}", response_model=TaskSummaryView)
async def get_task(task: OwnedTaskDep, service: TaskServiceDep) -> TaskSummaryView:
    return service.to_summary(await service.reconcile_abandoned_task(task))


@router.get("/{task_id}/messages/{message_id}", response_model=FinalMessageResponse)
async def get_final_message(
    message_id: str,
    task: OwnedTaskDep,
    service: TaskServiceDep,
) -> FinalMessageResponse:
    message = await service.get_final_message(task, message_id)
    if message is None:
        raise HTTPException(status_code=404)
    return FinalMessageResponse(message_id=message.id, text=message.text)


@router.post("/{task_id}/steer", response_model=TaskControlAccepted, status_code=202)
async def steer_task(
    request: SteeringRequest,
    task: OwnedTaskWriteDep,
    service: TaskServiceDep,
    idempotency_key: IdempotencyKeyHeader,
) -> TaskControlAccepted:
    command = await service.steer(task.partition(), task.id, request.instruction, idempotency_key)
    return TaskControlAccepted(command_id=command.id, sequence=command.sequence)


@router.post("/{task_id}/cancel", response_model=CancellationAccepted, status_code=202)
async def cancel_task(
    task: OwnedTaskWriteDep,
    service: TaskServiceDep,
    idempotency_key: IdempotencyKeyHeader,
) -> CancellationAccepted:
    command = await service.cancel(task.partition(), task.id, idempotency_key)
    return CancellationAccepted(command_id=command.id, sequence=command.sequence)


@router.get("/{task_id}/events", response_class=EventSourceResponse)
async def stream_task_events(
    request: Request,
    task: OwnedTaskDep,
    service: TaskServiceDep,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> AsyncIterator[ServerSentEvent]:
    task = await service.reconcile_abandoned_task(task)
    terminal = task.status in {
        TaskStatus.COMPLETED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
        TaskStatus.FAILED_CANCELLATION,
    }
    if terminal:
        for entry in service.snapshot_events(task):
            yield ServerSentEvent(
                data=entry.event.model_dump(mode="json", by_alias=True),
                event=entry.event.type.value,
                id=None,
            )
        return
    events_available = service.events is not None and await service.events.exists(task.id)
    if not events_available:
        for entry in service.snapshot_events(task):
            yield ServerSentEvent(
                data=entry.event.model_dump(mode="json", by_alias=True),
                event=entry.event.type.value,
                id=None,
            )
        if service.events is None:
            return
    current = last_event_id if last_event_id and _REDIS_CURSOR.fullmatch(last_event_id) else "0-0"
    while not await request.is_disconnected():
        assert service.events is not None
        entries = await service.events.read_after(task.id, current, block_ms=15000)
        for entry in entries:
            current = entry.stream_id
            event_type = entry.event.type.value
            yield ServerSentEvent(
                data=entry.event.model_dump(mode="json", by_alias=True),
                event=event_type,
                id=entry.stream_id,
            )
            if event_type in _TERMINAL_EVENTS:
                return
