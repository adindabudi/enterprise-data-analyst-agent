from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime
from typing import Any, Protocol, cast

from azure.core import MatchConditions
from azure.cosmos.aio import ContainerProxy
from azure.cosmos.exceptions import CosmosHttpResponseError, CosmosResourceNotFoundError
from eda_contracts.controls import CommandKind
from eda_contracts.tasks import TaskStatus

from .models import (
    TERMINAL,
    OperationKeyInput,
    OperationRecord,
    OperationStatus,
    RequiredOutput,
    RuntimeLocator,
    TaskCommand,
    TaskPartition,
    TaskRecord,
    canonical_json,
    canonical_operation_key,
    transition,
)


class RuntimeStateConflict(ValueError):
    pass


class RuntimeStateRepository(Protocol):
    async def create_task(self, task: TaskRecord, idempotency_key: str) -> TaskRecord: ...

    async def claim_initial_dispatch(self, task_id: str) -> bool: ...

    async def release_initial_dispatch(self, task_id: str) -> bool: ...

    async def fail_abandoned_initial_dispatch(self, task_id: str, *, stale_before: datetime) -> TaskRecord: ...

    async def get_owned_task(self, partition: TaskPartition, task_id: str) -> TaskRecord | None: ...

    async def resolve_task(self, task_id: str) -> TaskRecord | None: ...

    async def replace_active_attempt(
        self,
        task_id: str,
        attempt_id: str,
        *,
        expected_attempt_id: str | None,
    ) -> TaskRecord: ...

    async def ensure_active_sandbox(self, task_id: str, proposed_identifier: str) -> str: ...

    async def clear_active_sandbox(self, task_id: str, expected_identifier: str) -> TaskRecord | None: ...

    async def set_final_message(self, task_id: str, message_id: str) -> TaskRecord: ...

    async def ensure_required_outputs(
        self,
        task_id: str,
        requirements: tuple[RequiredOutput, ...],
        *,
        through_sequence: int = 0,
    ) -> TaskRecord: ...

    async def transition_task(self, task_id: str, status: TaskStatus, expected_checkpoint: int) -> TaskRecord: ...

    async def append_command(
        self,
        partition: TaskPartition,
        task_id: str,
        kind: CommandKind,
        text: str | None,
        idempotency_key: str,
        *,
        request_id: str | None = None,
        approved: bool | None = None,
    ) -> TaskCommand: ...

    async def pending_commands(self, task_id: str) -> list[TaskCommand]: ...

    async def commands(self, task_id: str, command_ids: list[str]) -> list[TaskCommand]: ...

    async def acknowledge_commands(self, task_id: str, through_sequence: int) -> TaskRecord: ...

    async def begin_operation(
        self, task: TaskRecord, step_name: str, canonical_input: dict[str, Any]
    ) -> OperationRecord: ...

    async def complete_operation(
        self, task: TaskRecord, operation_id: str, immutable_result_ref: str
    ) -> OperationRecord: ...


class InMemoryRuntimeStateRepository:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._tasks: dict[str, TaskRecord] = {}
        self._task_requests: dict[tuple[str, ...], str] = {}
        self._locators: dict[str, TaskPartition] = {}
        self._commands: dict[str, list[TaskCommand]] = {}
        self._command_requests: dict[tuple[str, str], TaskCommand] = {}
        self._operations: dict[str, OperationRecord] = {}

    async def create_task(self, task: TaskRecord, idempotency_key: str) -> TaskRecord:
        async with self._lock:
            request_key = (*task.partition().values(), idempotency_key)
            existing_id = self._task_requests.get(request_key)
            if existing_id is not None:
                return self._tasks[existing_id]
            self._tasks[task.id] = task
            self._task_requests[request_key] = task.id
            self._locators[task.id] = task.partition()
            self._commands[task.id] = []
            return task

    async def claim_initial_dispatch(self, task_id: str) -> bool:
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise RuntimeStateConflict("task is unavailable")
            if (
                task.initial_dispatch_claimed
                or task.active_attempt_id is not None
                or task.status is not TaskStatus.PLANNING
            ):
                return False
            self._tasks[task_id] = task.model_copy(
                update={"initial_dispatch_claimed": True, "updated_at": datetime.now(UTC)}
            )
            return True

    async def release_initial_dispatch(self, task_id: str) -> bool:
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise RuntimeStateConflict("task is unavailable")
            if not task.initial_dispatch_claimed or task.active_attempt_id is not None:
                return False
            self._tasks[task_id] = task.model_copy(
                update={"initial_dispatch_claimed": False, "updated_at": datetime.now(UTC)}
            )
            return True

    async def fail_abandoned_initial_dispatch(self, task_id: str, *, stale_before: datetime) -> TaskRecord:
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise RuntimeStateConflict("task is unavailable")
            if (
                task.status is not TaskStatus.PLANNING
                or not task.initial_dispatch_claimed
                or task.active_attempt_id is not None
                or task.updated_at >= stale_before
            ):
                return task
            updated = task.model_copy(
                update={
                    "status": TaskStatus.FAILED,
                    "checkpoint_sequence": task.checkpoint_sequence + 1,
                    "updated_at": datetime.now(UTC),
                }
            )
            self._tasks[task_id] = updated
            return updated

    async def get_owned_task(self, partition: TaskPartition, task_id: str) -> TaskRecord | None:
        task = self._tasks.get(task_id)
        return task if task is not None and task.partition() == partition else None

    async def resolve_task(self, task_id: str) -> TaskRecord | None:
        partition = self._locators.get(task_id)
        if partition is None:
            return None
        return await self.get_owned_task(partition, task_id)

    async def replace_active_attempt(
        self,
        task_id: str,
        attempt_id: str,
        *,
        expected_attempt_id: str | None,
    ) -> TaskRecord:
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise RuntimeStateConflict("task is unavailable")
            if task.active_attempt_id == attempt_id:
                return task
            if expected_attempt_id is None and task.status in TERMINAL:
                raise RuntimeStateConflict("initial dispatch is already terminal")
            if task.active_attempt_id != expected_attempt_id:
                raise RuntimeStateConflict("active attempt assignment conflict")
            updated = task.model_copy(
                update={
                    "active_attempt_id": attempt_id,
                    "updated_at": datetime.now(UTC),
                }
            )
            self._tasks[task_id] = updated
            return updated

    async def ensure_active_sandbox(self, task_id: str, proposed_identifier: str) -> str:
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise RuntimeStateConflict("task is unavailable")
            if task.active_sandbox_id is not None:
                return task.active_sandbox_id
            updated = task.model_copy(
                update={
                    "active_sandbox_id": proposed_identifier,
                    "updated_at": datetime.now(UTC),
                }
            )
            self._tasks[task_id] = updated
            return proposed_identifier

    async def clear_active_sandbox(self, task_id: str, expected_identifier: str) -> TaskRecord | None:
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return None
            if task.active_sandbox_id != expected_identifier:
                return task
            updated = task.model_copy(update={"active_sandbox_id": None, "updated_at": datetime.now(UTC)})
            self._tasks[task_id] = updated
            return updated

    async def ensure_required_outputs(
        self,
        task_id: str,
        requirements: tuple[RequiredOutput, ...],
        *,
        through_sequence: int = 0,
    ) -> TaskRecord:
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise RuntimeStateConflict("task is unavailable")
            if task.required_outputs is not None and task.required_outputs_sequence >= through_sequence:
                return task
            if task.status in TERMINAL or task.cancellation_requested:
                raise RuntimeStateConflict("cannot plan a terminal or cancelled task")
            if not 0 <= through_sequence <= task.command_sequence:
                raise RuntimeStateConflict("output contract command sequence is invalid")
            updated = TaskRecord.model_validate(
                {
                    **task.model_dump(),
                    "requiredOutputs": requirements,
                    "updatedAt": datetime.now(UTC),
                    "requiredOutputsSequence": through_sequence,
                }
            )
            self._tasks[task_id] = updated
            return updated

    async def set_final_message(self, task_id: str, message_id: str) -> TaskRecord:
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise RuntimeStateConflict("task is unavailable")
            if task.final_message_id is not None:
                if task.final_message_id != message_id:
                    raise RuntimeStateConflict("task final message conflict")
                return task
            updated = task.model_copy(update={"final_message_id": message_id, "updated_at": datetime.now(UTC)})
            self._tasks[task_id] = updated
            return updated

    async def transition_task(self, task_id: str, status: TaskStatus, expected_checkpoint: int) -> TaskRecord:
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise RuntimeStateConflict("task checkpoint conflict")
            if task.checkpoint_sequence == expected_checkpoint + 1 and task.status is status:
                return task
            if task.checkpoint_sequence != expected_checkpoint:
                raise RuntimeStateConflict("task checkpoint conflict")
            if status != task.status:
                transition(task.status, status)
            updated = task.model_copy(
                update={
                    "status": status,
                    "checkpoint_sequence": task.checkpoint_sequence + 1,
                    "updated_at": datetime.now(UTC),
                }
            )
            self._tasks[task_id] = updated
            return updated

    async def append_command(
        self,
        partition: TaskPartition,
        task_id: str,
        kind: CommandKind,
        text: str | None,
        idempotency_key: str,
        *,
        request_id: str | None = None,
        approved: bool | None = None,
    ) -> TaskCommand:
        async with self._lock:
            request_key = task_id, idempotency_key
            existing = self._command_requests.get(request_key)
            if existing is not None:
                return existing
            task = self._tasks.get(task_id)
            if task is None or task.partition() != partition:
                raise RuntimeStateConflict("task is unavailable")
            sequence = task.command_sequence + 1
            digest = hashlib.sha256(f"{task_id}\0{idempotency_key}".encode()).hexdigest()
            command = TaskCommand(
                id=f"cmd_{digest[:32]}",
                tenant_id=partition.tenant_id,
                owner_object_id=partition.owner_object_id,
                session_id=partition.session_id,
                task_id=task_id,
                sequence=sequence,
                kind=kind,
                text=text,
                request_id=request_id,
                approved=approved,
                created_at=datetime.now(UTC),
            )
            self._commands[task_id].append(command)
            self._command_requests[request_key] = command
            updates: dict[str, object] = {"command_sequence": sequence, "updated_at": datetime.now(UTC)}
            if kind is CommandKind.CANCEL:
                updates["cancellation_requested"] = True
            self._tasks[task_id] = task.model_copy(update=updates)
            return command

    async def pending_commands(self, task_id: str) -> list[TaskCommand]:
        task = self._tasks.get(task_id)
        if task is None:
            return []
        return [command for command in self._commands[task_id] if command.sequence > task.applied_command_sequence]

    async def commands(self, task_id: str, command_ids: list[str]) -> list[TaskCommand]:
        available = {command.id: command for command in self._commands.get(task_id, [])}
        return [available[command_id] for command_id in command_ids if command_id in available]

    async def acknowledge_commands(self, task_id: str, through_sequence: int) -> TaskRecord:
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None or through_sequence > task.command_sequence:
                raise RuntimeStateConflict("command acknowledgement conflict")
            if through_sequence <= task.applied_command_sequence:
                return task
            updated = task.model_copy(
                update={"applied_command_sequence": through_sequence, "updated_at": datetime.now(UTC)}
            )
            self._tasks[task_id] = updated
            return updated

    async def begin_operation(
        self, task: TaskRecord, step_name: str, canonical_input: dict[str, Any]
    ) -> OperationRecord:
        operation_key = canonical_operation_key(
            OperationKeyInput(workflow_instance_id=task.id, step_name=step_name, canonical_input=canonical_input)
        )
        async with self._lock:
            existing = self._operations.get(operation_key)
            if existing is not None:
                return existing
            operation = OperationRecord(
                id=operation_key,
                tenant_id=task.tenant_id,
                owner_object_id=task.owner_object_id,
                session_id=task.session_id,
                task_id=task.id,
                step_name=step_name,
                canonical_input_hash=hashlib.sha256(canonical_json(canonical_input)).hexdigest(),
                status=OperationStatus.IN_PROGRESS,
                updated_at=datetime.now(UTC),
            )
            self._operations[operation_key] = operation
            return operation

    async def complete_operation(
        self, task: TaskRecord, operation_id: str, immutable_result_ref: str
    ) -> OperationRecord:
        async with self._lock:
            operation = self._operations.get(operation_id)
            if operation is None or operation.task_id != task.id:
                raise RuntimeStateConflict("operation is unavailable")
            if operation.status is OperationStatus.COMPLETED:
                return operation
            updated = operation.model_copy(
                update={
                    "status": OperationStatus.COMPLETED,
                    "immutable_result_ref": immutable_result_ref,
                    "updated_at": datetime.now(UTC),
                }
            )
            self._operations[operation_id] = updated
            return updated


class CosmosRuntimeStateRepository:
    def __init__(self, workspace: ContainerProxy, runtime: ContainerProxy) -> None:
        self._workspace = workspace
        self._runtime = runtime

    async def create_task(self, task: TaskRecord, idempotency_key: str) -> TaskRecord:
        del idempotency_key
        try:
            document = await self._workspace.create_item(task.model_dump(mode="json", by_alias=True, exclude={"etag"}))
            created = TaskRecord.model_validate(document)
        except CosmosHttpResponseError as error:
            if error.status_code != 409:
                raise
            existing = await self.get_owned_task(task.partition(), task.id)
            if existing is None:
                raise RuntimeStateConflict("task creation conflict") from error
            created = existing
        locator = RuntimeLocator(
            id=created.id,
            tenant_id=created.tenant_id,
            owner_object_id=created.owner_object_id,
            session_id=created.session_id,
            expires_at=created.expires_at,
            ttl=max(1, int((created.expires_at - datetime.now(UTC)).total_seconds())),
        )
        try:
            await self._runtime.create_item(locator.model_dump(mode="json", by_alias=True))
        except CosmosHttpResponseError as error:
            if error.status_code != 409:
                raise
        return created

    async def claim_initial_dispatch(self, task_id: str) -> bool:
        task = await self.resolve_task(task_id)
        if task is None:
            raise RuntimeStateConflict("task is unavailable")
        if (
            task.initial_dispatch_claimed
            or task.active_attempt_id is not None
            or task.status is not TaskStatus.PLANNING
        ):
            return False
        updated = task.model_copy(update={"initial_dispatch_claimed": True, "updated_at": datetime.now(UTC)})
        try:
            await self._replace_task(updated, task.etag)
        except RuntimeStateConflict:
            return False
        return True

    async def release_initial_dispatch(self, task_id: str) -> bool:
        task = await self.resolve_task(task_id)
        if task is None:
            raise RuntimeStateConflict("task is unavailable")
        if not task.initial_dispatch_claimed or task.active_attempt_id is not None:
            return False
        updated = task.model_copy(update={"initial_dispatch_claimed": False, "updated_at": datetime.now(UTC)})
        await self._replace_task(updated, task.etag)
        return True

    async def fail_abandoned_initial_dispatch(self, task_id: str, *, stale_before: datetime) -> TaskRecord:
        task = await self.resolve_task(task_id)
        if task is None:
            raise RuntimeStateConflict("task is unavailable")
        if (
            task.status is not TaskStatus.PLANNING
            or not task.initial_dispatch_claimed
            or task.active_attempt_id is not None
            or task.updated_at >= stale_before
        ):
            return task
        updated = task.model_copy(
            update={
                "status": TaskStatus.FAILED,
                "checkpoint_sequence": task.checkpoint_sequence + 1,
                "updated_at": datetime.now(UTC),
            }
        )
        try:
            return await self._replace_task(updated, task.etag)
        except RuntimeStateConflict:
            winner = await self.resolve_task(task_id)
            if winner is None:
                raise
            return winner

    async def get_owned_task(self, partition: TaskPartition, task_id: str) -> TaskRecord | None:
        try:
            document = await self._workspace.read_item(item=task_id, partition_key=partition.values())
        except CosmosResourceNotFoundError:
            return None
        return TaskRecord.model_validate(document)

    async def resolve_task(self, task_id: str) -> TaskRecord | None:
        try:
            document = await self._runtime.read_item(item=task_id, partition_key=task_id)
        except CosmosResourceNotFoundError:
            return None
        locator = RuntimeLocator.model_validate(document)
        return await self.get_owned_task(locator.partition(), task_id)

    async def replace_active_attempt(
        self,
        task_id: str,
        attempt_id: str,
        *,
        expected_attempt_id: str | None,
    ) -> TaskRecord:
        task = await self.resolve_task(task_id)
        if task is None:
            raise RuntimeStateConflict("task is unavailable")
        if task.active_attempt_id == attempt_id:
            return task
        if expected_attempt_id is None and task.status in TERMINAL:
            raise RuntimeStateConflict("initial dispatch is already terminal")
        if task.active_attempt_id != expected_attempt_id:
            raise RuntimeStateConflict("active attempt assignment conflict")
        updated = task.model_copy(
            update={
                "active_attempt_id": attempt_id,
                "updated_at": datetime.now(UTC),
            }
        )
        try:
            return await self._replace_task(updated, task.etag)
        except RuntimeStateConflict as error:
            winner = await self.resolve_task(task_id)
            if winner is not None and winner.active_attempt_id == attempt_id:
                return winner
            raise RuntimeStateConflict("active attempt assignment conflict") from error

    async def ensure_active_sandbox(self, task_id: str, proposed_identifier: str) -> str:
        task = await self.resolve_task(task_id)
        if task is None:
            raise RuntimeStateConflict("task is unavailable")
        if task.active_sandbox_id is not None:
            return task.active_sandbox_id
        updated = task.model_copy(
            update={
                "active_sandbox_id": proposed_identifier,
                "updated_at": datetime.now(UTC),
            }
        )
        try:
            replaced = await self._replace_task(updated, task.etag)
            return replaced.active_sandbox_id or proposed_identifier
        except RuntimeStateConflict as error:
            # Single-reload CAS winner resolution: if another worker won, return it.
            reloaded = await self.resolve_task(task_id)
            if reloaded is not None and reloaded.active_sandbox_id is not None:
                return reloaded.active_sandbox_id
            raise RuntimeStateConflict("active sandbox assignment conflict") from error

    async def ensure_required_outputs(
        self,
        task_id: str,
        requirements: tuple[RequiredOutput, ...],
        *,
        through_sequence: int = 0,
    ) -> TaskRecord:
        for attempt in range(3):
            task = await self.resolve_task(task_id)
            if task is None:
                raise RuntimeStateConflict("task is unavailable")
            if task.required_outputs is not None and task.required_outputs_sequence >= through_sequence:
                return task
            if task.status in TERMINAL or task.cancellation_requested:
                raise RuntimeStateConflict("cannot plan a terminal or cancelled task")
            if not 0 <= through_sequence <= task.command_sequence:
                raise RuntimeStateConflict("output contract command sequence is invalid")
            updated = TaskRecord.model_validate(
                {
                    **task.model_dump(),
                    "requiredOutputs": requirements,
                    "updatedAt": datetime.now(UTC),
                    "requiredOutputsSequence": through_sequence,
                }
            )
            try:
                return await self._replace_task(updated, task.etag)
            except RuntimeStateConflict:
                if attempt == 2:
                    raise
        raise RuntimeStateConflict("output contract update conflict")

    async def clear_active_sandbox(self, task_id: str, expected_identifier: str) -> TaskRecord | None:
        task = await self.resolve_task(task_id)
        if task is None:
            return None
        if task.active_sandbox_id != expected_identifier:
            return task
        updated = task.model_copy(update={"active_sandbox_id": None, "updated_at": datetime.now(UTC)})
        try:
            return await self._replace_task(updated, task.etag)
        except RuntimeStateConflict:
            reloaded = await self.resolve_task(task_id)
            if reloaded is None:
                return None
            if reloaded.active_sandbox_id != expected_identifier:
                return reloaded
            retried = reloaded.model_copy(update={"active_sandbox_id": None, "updated_at": datetime.now(UTC)})
            return await self._replace_task(retried, reloaded.etag)

    async def transition_task(self, task_id: str, status: TaskStatus, expected_checkpoint: int) -> TaskRecord:
        task = await self.resolve_task(task_id)
        if task is None:
            raise RuntimeStateConflict("task checkpoint conflict")
        if task.checkpoint_sequence == expected_checkpoint + 1 and task.status is status:
            return task
        if task.checkpoint_sequence != expected_checkpoint:
            raise RuntimeStateConflict("task checkpoint conflict")
        if status != task.status:
            transition(task.status, status)
        updated = task.model_copy(
            update={
                "status": status,
                "checkpoint_sequence": task.checkpoint_sequence + 1,
                "updated_at": datetime.now(UTC),
            }
        )
        return await self._replace_task(updated, task.etag)

    async def set_final_message(self, task_id: str, message_id: str) -> TaskRecord:
        task = await self.resolve_task(task_id)
        if task is None:
            raise RuntimeStateConflict("task is unavailable")
        if task.final_message_id is not None:
            if task.final_message_id != message_id:
                raise RuntimeStateConflict("task final message conflict")
            return task
        updated = task.model_copy(update={"final_message_id": message_id, "updated_at": datetime.now(UTC)})
        try:
            return await self._replace_task(updated, task.etag)
        except RuntimeStateConflict:
            winner = await self.resolve_task(task_id)
            if winner is not None and winner.final_message_id == message_id:
                return winner
            raise

    async def append_command(
        self,
        partition: TaskPartition,
        task_id: str,
        kind: CommandKind,
        text: str | None,
        idempotency_key: str,
        *,
        request_id: str | None = None,
        approved: bool | None = None,
    ) -> TaskCommand:
        task = await self.get_owned_task(partition, task_id)
        if task is None:
            raise RuntimeStateConflict("task is unavailable")
        digest = hashlib.sha256(f"{task_id}\0{idempotency_key}".encode()).hexdigest()
        command_id = f"cmd_{digest[:32]}"
        try:
            existing_document = await self._workspace.read_item(item=command_id, partition_key=partition.values())
        except CosmosResourceNotFoundError:
            existing_document = None
        if existing_document is not None:
            return TaskCommand.model_validate(existing_document)
        sequence = task.command_sequence + 1
        command = TaskCommand(
            id=command_id,
            tenant_id=partition.tenant_id,
            owner_object_id=partition.owner_object_id,
            session_id=partition.session_id,
            task_id=task_id,
            sequence=sequence,
            kind=kind,
            text=text,
            request_id=request_id,
            approved=approved,
            created_at=datetime.now(UTC),
        )
        task_updates: dict[str, object] = {"command_sequence": sequence, "updated_at": datetime.now(UTC)}
        if kind is CommandKind.CANCEL:
            task_updates["cancellation_requested"] = True
        updated_task = task.model_copy(update=task_updates)
        operations = [
            (
                "replace",
                (task.id, updated_task.model_dump(mode="json", by_alias=True, exclude={"etag"})),
                {"if_match_etag": task.etag},
            ),
            ("create", (command.model_dump(mode="json", by_alias=True),)),
        ]
        try:
            await self._workspace.execute_item_batch(operations, partition_key=partition.values())
        except CosmosHttpResponseError as error:
            raise RuntimeStateConflict("command append conflict") from error
        return command

    async def pending_commands(self, task_id: str) -> list[TaskCommand]:
        task = await self.resolve_task(task_id)
        if task is None:
            return []
        parameters: list[dict[str, object]] = [
            {"name": "@taskId", "value": task_id},
            {"name": "@sequence", "value": task.applied_command_sequence},
        ]
        query = (
            "SELECT * FROM c WHERE c.recordType = 'taskCommand' AND c.taskId = @taskId "
            "AND c.sequence > @sequence ORDER BY c.sequence ASC"
        )
        raw_iterator = self._workspace.query_items(
            query=query, parameters=parameters, partition_key=task.partition().values()
        )
        iterator = cast(Any, raw_iterator)
        return [TaskCommand.model_validate(item) async for item in iterator]

    async def commands(self, task_id: str, command_ids: list[str]) -> list[TaskCommand]:
        task = await self.resolve_task(task_id)
        if task is None:
            return []
        loaded: list[TaskCommand] = []
        for command_id in command_ids:
            try:
                document = await self._workspace.read_item(
                    item=command_id,
                    partition_key=task.partition().values(),
                )
            except CosmosResourceNotFoundError:
                continue
            command = TaskCommand.model_validate(document)
            if command.task_id != task_id:
                continue
            loaded.append(command)
        return loaded

    async def acknowledge_commands(self, task_id: str, through_sequence: int) -> TaskRecord:
        task = await self.resolve_task(task_id)
        if task is None or through_sequence > task.command_sequence:
            raise RuntimeStateConflict("command acknowledgement conflict")
        if through_sequence <= task.applied_command_sequence:
            return task
        updated = task.model_copy(
            update={"applied_command_sequence": through_sequence, "updated_at": datetime.now(UTC)}
        )
        return await self._replace_task(updated, task.etag)

    async def begin_operation(
        self, task: TaskRecord, step_name: str, canonical_input: dict[str, Any]
    ) -> OperationRecord:
        operation_key = canonical_operation_key(
            OperationKeyInput(workflow_instance_id=task.id, step_name=step_name, canonical_input=canonical_input)
        )
        operation = OperationRecord(
            id=operation_key,
            tenant_id=task.tenant_id,
            owner_object_id=task.owner_object_id,
            session_id=task.session_id,
            task_id=task.id,
            step_name=step_name,
            canonical_input_hash=hashlib.sha256(canonical_json(canonical_input)).hexdigest(),
            status=OperationStatus.IN_PROGRESS,
            updated_at=datetime.now(UTC),
        )
        try:
            document = await self._workspace.create_item(operation.model_dump(mode="json", by_alias=True))
        except CosmosHttpResponseError as error:
            if error.status_code != 409:
                raise
            document = await self._workspace.read_item(item=operation_key, partition_key=task.partition().values())
        return OperationRecord.model_validate(document)

    async def complete_operation(
        self, task: TaskRecord, operation_id: str, immutable_result_ref: str
    ) -> OperationRecord:
        try:
            document = await self._workspace.read_item(item=operation_id, partition_key=task.partition().values())
        except CosmosResourceNotFoundError as error:
            raise RuntimeStateConflict("operation is unavailable") from error
        operation = OperationRecord.model_validate(document)
        if operation.status is OperationStatus.COMPLETED:
            return operation
        updated = operation.model_copy(
            update={
                "status": OperationStatus.COMPLETED,
                "immutable_result_ref": immutable_result_ref,
                "updated_at": datetime.now(UTC),
            }
        )
        try:
            document = await self._workspace.replace_item(
                item=operation_id,
                body=updated.model_dump(mode="json", by_alias=True, exclude={"etag"}),
                etag=operation.etag,
                match_condition=MatchConditions.IfNotModified,
            )
        except CosmosHttpResponseError as error:
            raise RuntimeStateConflict("operation completion conflict") from error
        return OperationRecord.model_validate(document)

    async def _replace_task(self, task: TaskRecord, etag: str) -> TaskRecord:
        try:
            document = await self._workspace.replace_item(
                item=task.id,
                body=task.model_dump(mode="json", by_alias=True, exclude={"etag"}),
                etag=etag,
                match_condition=MatchConditions.IfNotModified,
            )
        except CosmosHttpResponseError as error:
            raise RuntimeStateConflict("task update conflict") from error
        return TaskRecord.model_validate(document)
