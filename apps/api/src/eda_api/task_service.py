from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Protocol, cast

import httpx
from azure.core.exceptions import ServiceRequestError
from eda_contracts import ActivityEvent, ArtifactRef
from eda_contracts.controls import CommandKind
from eda_contracts.tasks import TaskStatus
from eda_runtime_state.events import ACTIVITY_EVENT, EventDraft, StreamEntry
from eda_runtime_state.messages import CanonicalMessage, MessageRepository
from eda_runtime_state.models import QueryResultRef, TaskPartition, TaskRecord, deterministic_task_id
from eda_runtime_state.tasks import RuntimeStateConflict, RuntimeStateRepository
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from eda_api.auth.models import Principal
from eda_api.chat.service import QueryRun
from eda_api.hosted_responses import HostedResponseAttempt, HostedResponseStatus
from eda_api.storage.inputs import InputArtifactWriter
from eda_api.storage.query_results import QueryResultWriter
from eda_api.storage.uploads import UploadRejected, UploadService

MAX_RECORDED_QUERY_CHARS = 8_000
INITIAL_DISPATCH_TIMEOUT = timedelta(minutes=10)


def _row_count(rows: str) -> int:
    try:
        parsed: object = json.loads(rows)
    except json.JSONDecodeError:
        return 0
    return len(cast(list[object], parsed)) if isinstance(parsed, list) else 0


class TaskEventStore(Protocol):
    async def exists(self, task_id: str) -> bool: ...

    async def read_after(self, task_id: str, cursor: str | None, *, block_ms: int = 15000) -> list[StreamEntry]: ...


class NullTaskEventStore:
    async def exists(self, task_id: str) -> bool:
        del task_id
        return False

    async def read_after(self, task_id: str, cursor: str | None, *, block_ms: int = 15000) -> list[StreamEntry]:
        del task_id, cursor, block_ms
        return []


class HostedTaskClient(Protocol):
    async def start(
        self,
        task_id: str,
        *,
        user_identity: str,
        previous_response_id: str | None = None,
    ) -> HostedResponseAttempt: ...

    async def get(self, response_id: str, *, user_identity: str) -> HostedResponseAttempt: ...

    async def cancel(self, response_id: str, *, user_identity: str) -> HostedResponseAttempt: ...

    async def close(self) -> object: ...


TERMINAL_TASK_STATUS = frozenset(
    {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.FAILED_CANCELLATION}
)
FAILED_HOSTED_RESPONSES = frozenset(
    {
        HostedResponseStatus.COMPLETED,
        HostedResponseStatus.FAILED,
        HostedResponseStatus.INCOMPLETE,
    }
)
logger = logging.getLogger(__name__)


class SourceMessageNotFoundError(ValueError):
    pass


class InputPipelineUnavailable(RuntimeError):
    pass


class TaskSummaryView(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        serialize_by_alias=True,
    )

    task_id: str = Field(pattern=r"^task_[A-Za-z0-9_-]{8,}$")
    session_id: str = Field(pattern=r"^ses_[A-Za-z0-9_-]{8,}$")
    status: TaskStatus
    checkpoint_sequence: int = Field(ge=0)
    active_attempt_id: str | None = None
    final_message_id: str | None = None


class TaskService:
    def __init__(
        self,
        repository: RuntimeStateRepository,
        message_repository: MessageRepository,
        hosted_client: HostedTaskClient,
        events: TaskEventStore | None = None,
        query_results: QueryResultWriter | None = None,
        *,
        uploads: UploadService | None = None,
        input_artifact_writer: InputArtifactWriter | None = None,
    ) -> None:
        self.repository = repository
        self.message_repository = message_repository
        self.events = events
        self.hosted_client = hosted_client
        self.query_results = query_results
        self.uploads = uploads
        self.input_artifact_writer = input_artifact_writer

    async def _store_inputs(self, task: TaskRecord) -> tuple[ArtifactRef, ...]:
        if not task.input_upload_ids:
            return ()
        if self.uploads is None or self.input_artifact_writer is None:
            raise InputPipelineUnavailable("input storage is unavailable")
        refs: list[ArtifactRef] = []
        for upload_id in task.input_upload_ids:
            upload, content = await self.uploads.read_clean_upload(task.partition(), task.session_id, upload_id)
            refs.append(await self.input_artifact_writer.write(task, upload, content))
        return tuple(refs)

    async def _store_query_runs(
        self, task: TaskRecord, runs: tuple[QueryRun, ...], executed_at: datetime
    ) -> tuple[QueryResultRef, ...]:
        if not runs or self.query_results is None:
            return ()
        stored: list[QueryResultRef] = []
        for index, run in enumerate(runs, start=1):
            content = run.rows.encode("utf-8")
            display_name = f"query-result-{index}.json"
            try:
                ref = await self.query_results.write(task, display_name, content)
            except Exception:
                # A durable run without its rows is worth more than no durable run at all.
                logger.exception("query result handoff failed for task %s", task.id)
                continue
            stored.append(
                QueryResultRef(
                    artifact_id=ref.artifact_id,
                    version=ref.version,
                    kind=ref.kind.value,
                    sha256=ref.sha256,
                    display_name=display_name,
                    query=run.query[:MAX_RECORDED_QUERY_CHARS],
                    query_sha256=hashlib.sha256(run.query.encode("utf-8")).hexdigest(),
                    row_count=_row_count(run.rows),
                    source_alias=run.source_alias,
                    message_id=run.message_id,
                    executed_at=executed_at,
                )
            )
        return tuple(stored)

    async def get_owned_task(self, principal: Principal, task_id: str) -> TaskRecord | None:
        task = await self.repository.resolve_task(task_id)
        if task is None or task.tenant_id != principal.tenant_id or task.owner_object_id != principal.owner_object_id:
            return None
        return task

    async def reconcile_abandoned_task(self, task: TaskRecord) -> TaskRecord:
        if task.status in TERMINAL_TASK_STATUS:
            return task
        if task.active_attempt_id is None:
            if task.initial_dispatch_claimed:
                return await self.repository.fail_abandoned_initial_dispatch(
                    task.id, stale_before=datetime.now(UTC) - INITIAL_DISPATCH_TIMEOUT
                )
            return task
        try:
            attempt = await self.hosted_client.get(
                task.active_attempt_id,
                user_identity=self._hosted_user_identity(task),
            )
        except Exception:
            logger.exception("hosted response reconciliation failed", extra={"task_id": task.id})
            return task
        if attempt.status is HostedResponseStatus.CANCELLED:
            cancelling = task
            if task.status is not TaskStatus.CANCELLING:
                cancelling = await self.repository.transition_task(
                    task.id,
                    TaskStatus.CANCELLING,
                    task.checkpoint_sequence,
                )
            return await self.repository.transition_task(
                task.id,
                TaskStatus.CANCELLED,
                cancelling.checkpoint_sequence,
            )
        if attempt.status not in FAILED_HOSTED_RESPONSES:
            return task
        return await self.repository.transition_task(task.id, TaskStatus.FAILED, task.checkpoint_sequence)

    def snapshot_events(self, task: TaskRecord) -> list[StreamEntry]:
        checkpoint = self._entry(
            EventDraft(
                session_id=task.session_id,
                task_id=task.id,
                type="task.checkpointed",
                payload={"status": task.status.value, "checkpointSequence": task.checkpoint_sequence},
            ),
            stream_id="snapshot-checkpoint",
        )
        if task.status not in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}:
            return [checkpoint]
        terminal_type = {
            TaskStatus.COMPLETED: "run.completed",
            TaskStatus.FAILED: "run.failed",
            TaskStatus.CANCELLED: "run.cancelled",
        }[task.status]
        terminal = self._entry(
            EventDraft(
                session_id=task.session_id,
                task_id=task.id,
                type=terminal_type,
                payload={"status": task.status.value, "finalMessageId": task.final_message_id},
            ),
            stream_id="snapshot-terminal",
        )
        return [checkpoint, terminal]

    @staticmethod
    def to_summary(task: TaskRecord) -> TaskSummaryView:
        return TaskSummaryView(
            task_id=task.id,
            session_id=task.session_id,
            status=task.status,
            checkpoint_sequence=task.checkpoint_sequence,
            active_attempt_id=task.active_attempt_id,
            final_message_id=task.final_message_id,
        )

    async def append_user_message(self, partition: TaskPartition, text: str, idempotency_key: str) -> CanonicalMessage:
        return await self.message_repository.append_user(partition, text, idempotency_key)

    async def get_final_message(self, task: TaskRecord, message_id: str) -> CanonicalMessage | None:
        if task.final_message_id != message_id:
            return None
        message = await self.message_repository.get_owned(task.partition(), message_id)
        if message is None or message.task_id != task.id or message.role != "assistant":
            return None
        return message

    async def start_task(
        self,
        partition: TaskPartition,
        idempotency_key: str,
        message_id: str,
        handoff_context: str | None = None,
        query_runs: tuple[QueryRun, ...] = (),
        *,
        input_upload_ids: tuple[str, ...] = (),
    ) -> TaskRecord:
        message = await self.message_repository.get_owned(partition, message_id)
        if message is None:
            raise SourceMessageNotFoundError("source message is unavailable")
        if len(input_upload_ids) != len(set(input_upload_ids)):
            raise UploadRejected("duplicate input upload IDs")
        upload_ids = tuple(sorted(input_upload_ids))
        task_id = deterministic_task_id(partition, idempotency_key)
        existing = await self.repository.get_owned_task(partition, task_id)
        if existing is not None:
            self._check_input_binding(existing, message_id, upload_ids)
            if existing.active_attempt_id is not None or existing.status in TERMINAL_TASK_STATUS:
                return existing
            if existing.initial_dispatch_claimed:
                recovered = await self.reconcile_abandoned_task(existing)
                if recovered.active_attempt_id is not None or recovered.status in TERMINAL_TASK_STATUS:
                    return recovered
                raise RuntimeStateConflict("task submission already in progress; reconciliation required")
            return await self._dispatch_initial_task(existing)
        now = datetime.now(UTC)
        task = TaskRecord(
            id=task_id,
            tenant_id=partition.tenant_id,
            owner_object_id=partition.owner_object_id,
            session_id=partition.session_id,
            status=TaskStatus.PLANNING,
            checkpoint_sequence=0,
            command_sequence=0,
            applied_command_sequence=0,
            sourceMessageId=message.id,
            handoff_context=handoff_context,
            input_upload_ids=upload_ids,
            created_at=now,
            updated_at=now,
            expires_at=now + timedelta(days=30),
        )
        input_artifacts = await self._store_inputs(task)
        task = TaskRecord.model_validate(
            {
                **task.model_dump(by_alias=False),
                "input_artifacts": input_artifacts,
                "query_results": await self._store_query_runs(task, query_runs, now),
            }
        )
        created = await self.repository.create_task(task, idempotency_key)
        self._check_input_binding(created, message_id, upload_ids)
        if created.input_artifacts != input_artifacts:
            raise RuntimeStateConflict("task input artifact conflict")
        return await self._dispatch_initial_task(created)

    async def _dispatch_initial_task(self, created: TaskRecord) -> TaskRecord:
        if created.active_attempt_id is not None or created.status in TERMINAL_TASK_STATUS:
            return created
        if not await self.repository.claim_initial_dispatch(created.id):
            winner = await self.repository.get_owned_task(created.partition(), created.id)
            if winner is not None and (winner.active_attempt_id is not None or winner.status in TERMINAL_TASK_STATUS):
                return winner
            raise RuntimeStateConflict("task submission already in progress; reconciliation required")
        try:
            attempt = await self.hosted_client.start(
                created.id,
                user_identity=self._hosted_user_identity(created),
            )
        except (ServiceRequestError, httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
            await self.repository.release_initial_dispatch(created.id)
            raise
        try:
            return await self.repository.replace_active_attempt(
                created.id,
                attempt.id,
                expected_attempt_id=None,
            )
        except ValueError:
            try:
                await self.hosted_client.cancel(
                    attempt.id,
                    user_identity=self._hosted_user_identity(created),
                )
            except Exception:
                logger.exception("duplicate hosted response cancellation failed", extra={"task_id": created.id})
            winner = await self.repository.get_owned_task(created.partition(), created.id)
            if winner is None or (winner.active_attempt_id is None and winner.status not in TERMINAL_TASK_STATUS):
                raise
            return winner

    @staticmethod
    def _check_input_binding(task: TaskRecord, message_id: str, upload_ids: tuple[str, ...]) -> None:
        if task.source_message_id != message_id or task.input_upload_ids != upload_ids:
            raise RuntimeStateConflict("idempotency key is already bound to different task inputs")

    async def steer(self, partition: TaskPartition, task_id: str, instruction: str, idempotency_key: str):
        command = await self.repository.append_command(
            partition,
            task_id,
            CommandKind.STEER,
            instruction,
            idempotency_key,
        )
        return command

    async def cancel(self, partition: TaskPartition, task_id: str, idempotency_key: str):
        before = await self.repository.get_owned_task(partition, task_id)
        command = await self.repository.append_command(
            partition,
            task_id,
            CommandKind.CANCEL,
            None,
            idempotency_key,
        )
        if before is not None and command.sequence > before.command_sequence and before.active_attempt_id is not None:
            try:
                await self.hosted_client.cancel(
                    before.active_attempt_id,
                    user_identity=self._hosted_user_identity(before),
                )
            except Exception:
                logger.exception("hosted response cancellation failed", extra={"task_id": task_id})
        return command

    async def resume_auth(self, partition: TaskPartition, task_id: str, receipt: str):
        command = await self.repository.append_command(
            partition,
            task_id,
            CommandKind.AUTH_RESUMED,
            None,
            f"fabric-auth:{receipt}",
        )
        return command

    @staticmethod
    def _hosted_user_identity(task: TaskRecord) -> str:
        material = f"{task.tenant_id}\0{task.owner_object_id}".encode()
        return f"usr_{hashlib.sha256(material).hexdigest()[:32]}"

    @staticmethod
    def _entry(draft: EventDraft, *, stream_id: str) -> StreamEntry:
        body = draft.body()
        body["sequence"] = 0
        return StreamEntry(stream_id=stream_id, event=cast(ActivityEvent, ACTIVITY_EVENT.validate_python(body)))
