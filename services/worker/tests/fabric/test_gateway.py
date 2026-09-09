from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from eda_contracts.controls import CommandKind
from eda_contracts.tasks import TaskStatus
from eda_runtime_state.models import TaskRecord
from eda_runtime_state.tasks import InMemoryRuntimeStateRepository
from eda_worker.fabric.config import SemanticModelTarget
from eda_worker.fabric.contracts import FabricPrincipal, FabricQueryOperation, FabricQueryPurpose
from eda_worker.fabric.errors import FabricProviderError
from eda_worker.fabric.gateway import FabricIQGateway, InMemoryFabricResultStore
from eda_worker.fabric.planner import FabricPlannerResult


class RecordingPlanner:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls = 0

    async def run(self, operation: FabricQueryOperation, *, semantic_model_id: UUID) -> FabricPlannerResult:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return FabricPlannerResult(
            summary=f"{operation.semantic_model} returned 42.",
            provider_calls=("GetSemanticModelSchema", "ExecuteQuery"),
            execute_attempts=1,
        )


def principal() -> FabricPrincipal:
    return FabricPrincipal(
        tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
        owner_object_id=UUID("22222222-2222-2222-2222-222222222222"),
        audience=UUID("33333333-3333-3333-3333-333333333333"),
    )


def task() -> TaskRecord:
    now = datetime.now(UTC)
    owner = principal()
    return TaskRecord(
        id="task_fabric_gateway_1234",
        tenant_id=owner.tenant_id,
        owner_object_id=owner.owner_object_id,
        session_id="ses_1234567890abcdef",
        status=TaskStatus.ACQUIRING_DATA,
        checkpoint_sequence=2,
        command_sequence=0,
        applied_command_sequence=0,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(days=1),
    )


def query() -> FabricQueryOperation:
    return FabricQueryOperation(
        semantic_model="sales",
        purpose=FabricQueryPurpose.AGGREGATE,
        question="Total revenue?",
    )


async def gateway(planner: RecordingPlanner) -> tuple[FabricIQGateway, InMemoryRuntimeStateRepository]:
    repository = InMemoryRuntimeStateRepository()
    current = task()
    await repository.create_task(current, "fabric-gateway-task")
    instance = FabricIQGateway(
        planner=planner,
        runtime=repository,
        result_store=InMemoryFabricResultStore(),
        models={
            "sales": SemanticModelTarget(
                modelId=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
                description="Curated sales measures.",
            )
        },
    )
    return instance, repository


@pytest.mark.asyncio
async def test_alias_resolves_before_planner_and_operation_replays_by_invocation() -> None:
    planner = RecordingPlanner()
    instance, _ = await gateway(planner)

    first = await instance.query(task().id, "invocation_1234", principal(), query())
    second = await instance.query(task().id, "invocation_1234", principal(), query())

    assert first == second
    assert first.status == "ok"
    assert first.query_ref is not None
    assert planner.calls == 1


@pytest.mark.asyncio
async def test_raw_uuid_alias_and_wrong_owner_fail_before_planner() -> None:
    planner = RecordingPlanner()
    instance, _ = await gateway(planner)
    raw_uuid = query().model_copy(update={"semantic_model": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"})
    other = principal().model_copy(update={"owner_object_id": UUID("44444444-4444-4444-4444-444444444444")})

    with pytest.raises(ValueError, match="configured alias"):
        await instance.query(task().id, "invocation_1234", principal(), raw_uuid)
    with pytest.raises(ValueError, match="owner"):
        await instance.query(task().id, "invocation_5678", other, query())
    assert planner.calls == 0


@pytest.mark.asyncio
async def test_permission_denial_is_nonretryable_authorization_result() -> None:
    planner = RecordingPlanner(FabricProviderError(status_code=403, code="build_permission_denied"))
    instance, _ = await gateway(planner)

    result = await instance.query(task().id, "invocation_1234", principal(), query())

    assert result.status == "authorization_error"
    assert result.error_code == "build_permission_denied"
    assert result.retryable is False
    assert planner.calls == 1


@pytest.mark.asyncio
async def test_cancellation_returns_without_planner_call() -> None:
    planner = RecordingPlanner()
    instance, repository = await gateway(planner)
    current = task()
    await repository.append_command(current.partition(), current.id, CommandKind.CANCEL, None, "cancel-fabric")

    result = await instance.query(current.id, "invocation_1234", principal(), query())

    assert result.status == "cancelled"
    assert planner.calls == 0
