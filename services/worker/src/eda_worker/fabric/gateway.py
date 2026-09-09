from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from eda_contracts import ArtifactKind, ArtifactRef
from eda_fabric_auth import FabricAuthorizationRequired
from eda_runtime_state.models import OperationRecord, OperationStatus, TaskRecord

from eda_worker.fabric.config import SemanticModelTarget
from eda_worker.fabric.contracts import FabricPrincipal, FabricQueryOperation, FabricQueryResult
from eda_worker.fabric.errors import FabricProviderError, run_with_transport_retries
from eda_worker.fabric.mcp_client import FabricMcpToolError
from eda_worker.fabric.planner import FabricPlannerResult
from eda_worker.fabric.provenance import FabricQueryDocument


class FabricPlanner(Protocol):
    async def run(
        self,
        operation: FabricQueryOperation,
        *,
        semantic_model_id: UUID,
    ) -> FabricPlannerResult: ...


class FabricRuntimeRepository(Protocol):
    async def resolve_task(self, task_id: str) -> TaskRecord | None: ...

    async def begin_operation(
        self,
        task: TaskRecord,
        step_name: str,
        canonical_input: dict[str, Any],
    ) -> OperationRecord: ...

    async def complete_operation(
        self,
        task: TaskRecord,
        operation_id: str,
        immutable_result_ref: str,
    ) -> OperationRecord: ...


class FabricResultStore(Protocol):
    async def persist(self, task: TaskRecord, result: FabricQueryResult) -> str: ...

    async def load(self, task: TaskRecord, immutable_ref: str) -> FabricQueryResult: ...


class FabricResultArtifactStore(Protocol):
    async def persist_bytes(
        self,
        task_id: str,
        kind: ArtifactKind,
        display_name: str,
        content: bytes,
    ) -> ArtifactRef: ...

    async def read_bytes(self, task_id: str, artifact: ArtifactRef) -> bytes: ...


class FabricEvidenceRecorder(Protocol):
    async def record(
        self,
        *,
        task: TaskRecord,
        query_ref: str,
        operation: FabricQueryOperation,
        target: SemanticModelTarget,
        planned: FabricPlannerResult,
        started_at: datetime,
        completed_at: datetime,
    ) -> FabricQueryDocument: ...


class InMemoryFabricResultStore:
    def __init__(self) -> None:
        self._results: dict[tuple[str, str], FabricQueryResult] = {}

    async def persist(self, task: TaskRecord, result: FabricQueryResult) -> str:
        payload = result.model_dump_json(exclude_none=True)
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        immutable_ref = f"fabric-result:{digest}"
        key = task.id, immutable_ref
        existing = self._results.get(key)
        if existing is not None and existing != result:
            raise ValueError("Fabric result reference conflict")
        self._results[key] = result
        return immutable_ref

    async def load(self, task: TaskRecord, immutable_ref: str) -> FabricQueryResult:
        result = self._results.get((task.id, immutable_ref))
        if result is None:
            raise ValueError("Fabric result is unavailable")
        return result


class ArtifactFabricResultStore:
    def __init__(self, artifacts: FabricResultArtifactStore) -> None:
        self._artifacts = artifacts

    async def persist(self, task: TaskRecord, result: FabricQueryResult) -> str:
        payload = result.model_dump_json(by_alias=True, exclude_none=True).encode("utf-8")
        artifact = await self._artifacts.persist_bytes(
            task.id,
            ArtifactKind.MANIFEST,
            "fabric-operation-result.json",
            payload,
        )
        return f"{artifact.artifact_id}@{artifact.version}@{artifact.sha256}"

    async def load(self, task: TaskRecord, immutable_ref: str) -> FabricQueryResult:
        parts = immutable_ref.split("@")
        if len(parts) != 3 or not parts[1].isdigit():
            raise ValueError("Fabric immutable operation result reference is malformed")
        artifact = ArtifactRef(
            artifact_id=parts[0],
            version=int(parts[1]),
            kind=ArtifactKind.MANIFEST,
            sha256=parts[2],
        )
        payload = await self._artifacts.read_bytes(task.id, artifact)
        return FabricQueryResult.model_validate_json(payload)


class FabricIQGateway:
    def __init__(
        self,
        *,
        planner: FabricPlanner | None = None,
        planner_factory: Callable[[FabricPrincipal], FabricPlanner] | None = None,
        runtime: FabricRuntimeRepository,
        result_store: FabricResultStore,
        models: Mapping[str, SemanticModelTarget],
        evidence_recorder: FabricEvidenceRecorder | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        if (planner is None) == (planner_factory is None):
            raise ValueError("Fabric gateway requires exactly one planner source")
        self._planner = planner
        self._planner_factory = planner_factory
        self._runtime = runtime
        self._result_store = result_store
        self._models = dict(models)
        self._evidence_recorder = evidence_recorder
        self._sleep = sleep

    async def query(
        self,
        task_id: str,
        invocation_id: str,
        principal: FabricPrincipal,
        operation: FabricQueryOperation,
    ) -> FabricQueryResult:
        target = self._models.get(operation.semantic_model)
        if target is None:
            raise ValueError("Fabric semantic model must be a configured alias")
        task = await self._runtime.resolve_task(task_id)
        if task is None:
            raise ValueError("Fabric task is unavailable")
        if task.tenant_id != principal.tenant_id or task.owner_object_id != principal.owner_object_id:
            raise ValueError("Fabric task owner does not match the trusted principal")
        if task.cancellation_requested:
            return FabricQueryResult(status="cancelled", summary="Task cancellation was requested.")
        canonical_input = {
            "invocationId": invocation_id,
            "operation": operation.model_dump(mode="json"),
        }
        started = await self._runtime.begin_operation(task, "fabric.query", canonical_input)
        if started.status is OperationStatus.COMPLETED:
            if started.immutable_result_ref is None:
                raise ValueError("completed Fabric operation has no immutable result")
            return await self._result_store.load(task, started.immutable_result_ref)

        planner_started_at = datetime.now(UTC)
        planner = self._planner or self._require_planner_factory()(principal)

        async def run_planner() -> FabricPlannerResult:
            return await planner.run(operation, semantic_model_id=target.model_id)

        try:
            if self._sleep is None:
                planned = await run_with_transport_retries(run_planner)
            else:
                planned = await run_with_transport_retries(run_planner, sleep=self._sleep)
            query_ref = _query_ref(task.id, invocation_id)
            artifact_refs: tuple[ArtifactRef, ...] = ()
            if self._evidence_recorder is not None:
                evidence = await self._evidence_recorder.record(
                    task=task,
                    query_ref=query_ref,
                    operation=operation,
                    target=target,
                    planned=planned,
                    started_at=planner_started_at,
                    completed_at=datetime.now(UTC),
                )
                artifact_refs = (evidence.result_artifact,)
            result = FabricQueryResult(
                status="ok",
                summary=planned.summary,
                query_ref=query_ref,
                artifact_refs=artifact_refs,
            )
        except FabricAuthorizationRequired:
            raise
        except FabricProviderError as error:
            if error.status_code == 401:
                raise FabricAuthorizationRequired("Fabric authorization is required.") from None
            if error.status_code != 403:
                raise
            result = FabricQueryResult(
                status="authorization_error",
                summary="Fabric access is not authorized for this semantic model.",
                query_ref=_query_ref(task.id, invocation_id),
                error_code=error.code,
                retryable=False,
            )
        except FabricMcpToolError as error:
            lowered = error.code.casefold()
            if not any(marker in lowered for marker in ("403", "forbidden", "authorization", "permission")):
                raise
            result = FabricQueryResult(
                status="authorization_error",
                summary="Fabric access is not authorized for this semantic model.",
                query_ref=_query_ref(task.id, invocation_id),
                error_code=error.code,
                retryable=False,
            )
        immutable_ref = await self._result_store.persist(task, result)
        await self._runtime.complete_operation(task, started.id, immutable_ref)
        return result

    def _require_planner_factory(self) -> Callable[[FabricPrincipal], FabricPlanner]:
        if self._planner_factory is None:
            raise RuntimeError("Fabric planner factory is unavailable")
        return self._planner_factory


def _query_ref(task_id: str, invocation_id: str) -> str:
    digest = hashlib.sha256(json.dumps([task_id, invocation_id], separators=(",", ":")).encode("utf-8")).hexdigest()
    return f"fabric-query-{digest[:32]}"
