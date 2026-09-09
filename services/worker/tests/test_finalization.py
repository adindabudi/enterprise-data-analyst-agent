from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from eda_contracts import ArtifactKind
from eda_contracts.tasks import TaskStatus
from eda_runtime_state.models import RequiredOutput, TaskRecord
from eda_runtime_state.tasks import InMemoryRuntimeStateRepository
from eda_worker.finalization import CoreTaskFinalizer
from eda_worker.history.models import CanonicalMessage, SessionPartition
from eda_worker.history.repository import InMemoryProjectionRepository
from eda_worker.sandbox.gateway import InMemoryArtifactGatewayStore
from eda_worker.tools.contracts import ValidationProfile


class Sandbox:
    def __init__(self) -> None:
        self.stopped: list[str] = []

    async def stop_task(self, task_id: str) -> None:
        self.stopped.append(task_id)


def task() -> TaskRecord:
    now = datetime.now(UTC)
    return TaskRecord(
        id="task_12345678",
        tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
        owner_object_id=UUID("22222222-2222-2222-2222-222222222222"),
        session_id="ses_12345678",
        status=TaskStatus.PUBLISHING,
        checkpoint_sequence=6,
        command_sequence=0,
        applied_command_sequence=0,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(days=1),
    )


@pytest.mark.asyncio
async def test_finalizer_requires_published_artifact_and_commits_canonical_message() -> None:
    runtime = InMemoryRuntimeStateRepository()
    value = task()
    await runtime.create_task(value, "request-12345678")
    artifacts = InMemoryArtifactGatewayStore()
    messages = InMemoryProjectionRepository()
    sandbox = Sandbox()
    finalizer = CoreTaskFinalizer(runtime, artifacts, messages, sandbox)

    assert await finalizer.validate_outputs({"taskId": value.id}) == {
        "outcome": "failed",
        "reportRef": "no-published-artifacts",
    }

    candidate = await artifacts.persist_bytes(value.id, ArtifactKind.XLSX, "analysis.xlsx", b"xlsx")
    report = await artifacts.persist_validation(
        value.id,
        candidate,
        profile=ValidationProfile.CORE_XLSX,
        report=b'{"status":"passed"}',
        status="passed",
    )
    published = await artifacts.publish(value.id, candidate, report)

    validation = await finalizer.validate_outputs({"taskId": value.id})
    first = await finalizer.publish_outputs({"taskId": value.id})
    second = await finalizer.publish_outputs({"taskId": value.id})
    updated = await runtime.resolve_task(value.id)
    canonical = await messages.load_canonical(
        SessionPartition(
            tenant_id=value.tenant_id,
            owner_object_id=value.owner_object_id,
            session_id=value.session_id,
        ),
        [first["finalMessageId"]],
    )

    assert validation["outcome"] == "passed"
    assert first == second
    assert updated is not None and updated.final_message_id == first["finalMessageId"]
    assert isinstance(canonical[0], CanonicalMessage)
    assert published.version == 2
    assert sandbox.stopped == [value.id, value.id]


@pytest.mark.asyncio
async def test_chat_completion_commits_agent_text_without_requiring_artifacts() -> None:
    runtime = InMemoryRuntimeStateRepository()
    value = task().model_copy(update={"status": TaskStatus.ANALYZING})
    await runtime.create_task(value, "request-chat-12345678")
    messages = InMemoryProjectionRepository()
    sandbox = Sandbox()
    finalizer = CoreTaskFinalizer(runtime, InMemoryArtifactGatewayStore(), messages, sandbox)

    first = await finalizer.complete_chat({"taskId": value.id, "text": "The answer is four."})
    second = await finalizer.complete_chat({"taskId": value.id, "text": "The answer is four."})
    updated = await runtime.resolve_task(value.id)
    canonical = await messages.load_canonical(
        SessionPartition(
            tenant_id=value.tenant_id,
            owner_object_id=value.owner_object_id,
            session_id=value.session_id,
        ),
        [first["finalMessageId"]],
    )

    assert first == second
    assert updated is not None and updated.final_message_id == first["finalMessageId"]
    assert canonical[0] is not None and canonical[0].text == "The answer is four."
    assert sandbox.stopped == [value.id, value.id]


@pytest.mark.asyncio
async def test_failure_releases_the_task_scoped_sandbox() -> None:
    runtime = InMemoryRuntimeStateRepository()
    value = task().model_copy(update={"status": TaskStatus.ANALYZING})
    await runtime.create_task(value, "request-failed-12345678")
    sandbox = Sandbox()
    finalizer = CoreTaskFinalizer(
        runtime,
        InMemoryArtifactGatewayStore(),
        InMemoryProjectionRepository(),
        sandbox,
    )

    assert await finalizer.fail_task({"taskId": value.id}) == {"outcome": "failed"}
    assert sandbox.stopped == [value.id]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "profile", "status", "expected"),
    [
        (ArtifactKind.DATA, ValidationProfile.CORE_XLSX, "passed", "failed"),
        (ArtifactKind.HTML, ValidationProfile.CORE_HTML, "passed", "failed"),
        (ArtifactKind.HTML, ValidationProfile.WEB_ARTIFACT_HTML, "failed", "failed"),
        (ArtifactKind.HTML, ValidationProfile.WEB_ARTIFACT_HTML, "passed", "passed"),
    ],
)
async def test_dashboard_completion_requires_published_web_validation(
    kind: ArtifactKind, profile: ValidationProfile, status: str, expected: str,
) -> None:
    runtime = InMemoryRuntimeStateRepository()
    value = task()
    await runtime.create_task(value, "request-dashboard-12345678")
    artifacts = InMemoryArtifactGatewayStore()
    candidate = await artifacts.persist_bytes(value.id, kind, "result.html", b"result")
    report = await artifacts.persist_validation(
        value.id, candidate, profile=profile, report=b"validation", status=status,
    )
    if status == "passed":
        await artifacts.publish(value.id, candidate, report)
    finalizer = CoreTaskFinalizer(runtime, artifacts, InMemoryProjectionRepository(), Sandbox())

    result = await finalizer.validate_outputs({
        "taskId": value.id, "requiredProfile": "web_artifact_html",
    })

    assert result["outcome"] == expected


@pytest.mark.asyncio
async def test_dashboard_completion_refuses_unfinished_plan() -> None:
    runtime = InMemoryRuntimeStateRepository()
    value = task()
    await runtime.create_task(value, "request-plan-12345678")
    artifacts = InMemoryArtifactGatewayStore()
    candidate = await artifacts.persist_bytes(value.id, ArtifactKind.HTML, "dashboard.html", b"html")
    report = await artifacts.persist_validation(
        value.id, candidate, ValidationProfile.WEB_ARTIFACT_HTML, b"passed", "passed",
    )
    await artifacts.publish(value.id, candidate, report)
    finalizer = CoreTaskFinalizer(
        runtime, artifacts, InMemoryProjectionRepository(), Sandbox(), OpenTodos("Reconcile totals"),
    )
    assert await finalizer.validate_outputs({
        "taskId": value.id, "requiredProfile": "web_artifact_html",
    }) == {"outcome": "failed", "reportRef": "unfinished-plan"}


class OpenTodos:
    def __init__(self, *titles: str) -> None:
        self._titles = titles

    async def open_todos(self, task: object) -> tuple[str, ...]:
        del task
        return self._titles


@pytest.mark.asyncio
@pytest.mark.parametrize(("requirements", "published_kind", "profile", "open_todos", "report_ref"), [
    (None, ArtifactKind.XLSX, ValidationProfile.CORE_XLSX, (), "missing-output-contract"),
    ((RequiredOutput(kind=ArtifactKind.XLSX),), None, ValidationProfile.CORE_XLSX, (),
     "missing-required-outputs:xlsx(0/1)"),
    ((RequiredOutput(kind=ArtifactKind.XLSX),), ArtifactKind.XLSX, ValidationProfile.CORE_HTML, (),
     "missing-required-outputs:xlsx(0/1)"),
    ((RequiredOutput(kind=ArtifactKind.XLSX, minimum_count=2),), ArtifactKind.XLSX,
     ValidationProfile.CORE_XLSX, (), "missing-required-outputs:xlsx(1/2)"),
    ((RequiredOutput(kind=ArtifactKind.HTML), RequiredOutput(kind=ArtifactKind.XLSX)), ArtifactKind.XLSX,
     ValidationProfile.CORE_XLSX, (), "missing-required-outputs:html(0/1)"),
    ((RequiredOutput(kind=ArtifactKind.HTML),), ArtifactKind.HTML, ValidationProfile.CORE_HTML, (),
     "missing-required-outputs:html(0/1)"),
    ((RequiredOutput(kind=ArtifactKind.XLSX),), ArtifactKind.XLSX, ValidationProfile.CORE_XLSX,
     ("Reconcile totals",), "unfinished-plan"),
    ((RequiredOutput(kind=ArtifactKind.XLSX),), ArtifactKind.XLSX, ValidationProfile.CORE_XLSX, (), None),
    ((), None, ValidationProfile.CORE_XLSX, (), None),
])
async def test_completion_validates_requested_outputs_without_a_default_html_profile(
    requirements: tuple[RequiredOutput, ...] | None,
    published_kind: ArtifactKind | None,
    profile: ValidationProfile,
    open_todos: tuple[str, ...],
    report_ref: str | None,
) -> None:
    runtime = InMemoryRuntimeStateRepository()
    value = TaskRecord.model_validate({**task().model_dump(), "requiredOutputs": requirements})
    await runtime.create_task(value, "request-contract-validation-12345678")
    artifacts = InMemoryArtifactGatewayStore()
    if published_kind is not None:
        candidate = await artifacts.persist_bytes(
            value.id, published_kind, f"result.{published_kind.value}", b"result",
        )
        report = await artifacts.persist_validation(value.id, candidate, profile, b"passed", "passed")
        await artifacts.publish(value.id, candidate, report)
    finalizer = CoreTaskFinalizer(
        runtime, artifacts, InMemoryProjectionRepository(), Sandbox(), OpenTodos(*open_todos),
    )

    result = await finalizer.validate_outputs({"taskId": value.id, "requireOutputContract": True})

    if report_ref is None:
        assert result["outcome"] == "passed"
    else:
        assert result == {"outcome": "failed", "reportRef": report_ref}


@pytest.mark.asyncio
async def test_completion_requires_every_declared_deliverable() -> None:
    runtime = InMemoryRuntimeStateRepository()
    value = TaskRecord.model_validate({
        **task().model_dump(),
        "requiredOutputs": [{"kind": "html", "minimumCount": 1}, {"kind": "xlsx", "minimumCount": 1}],
    })
    await runtime.create_task(value, "request-output-contract-12345678")
    artifacts = InMemoryArtifactGatewayStore()
    dashboard = await artifacts.persist_bytes(value.id, ArtifactKind.HTML, "dashboard.html", b"html")
    report = await artifacts.persist_validation(
        value.id, dashboard, ValidationProfile.WEB_ARTIFACT_HTML, b"passed", "passed",
    )
    await artifacts.publish(value.id, dashboard, report)
    finalizer = CoreTaskFinalizer(runtime, artifacts, InMemoryProjectionRepository(), Sandbox())

    result = await finalizer.validate_outputs({"taskId": value.id, "requiredProfile": "web_artifact_html"})

    assert result["outcome"] == "failed"
    assert "xlsx" in result["reportRef"]

    workbook = await artifacts.persist_bytes(value.id, ArtifactKind.XLSX, "analysis.xlsx", b"xlsx")
    report = await artifacts.persist_validation(value.id, workbook, ValidationProfile.CORE_XLSX, b"passed", "passed")
    await artifacts.publish(value.id, workbook, report)
    assert (await finalizer.validate_outputs({"taskId": value.id, "requiredProfile": "web_artifact_html"}))["outcome"] == "passed"


@pytest.mark.asyncio
async def test_a_run_that_abandoned_its_plan_says_so_in_the_final_message() -> None:
    runtime = InMemoryRuntimeStateRepository()
    value = task().model_copy(update={"status": TaskStatus.ANALYZING})
    await runtime.create_task(value, "request-chat-22345678")
    messages = InMemoryProjectionRepository()
    finalizer = CoreTaskFinalizer(
        runtime,
        InMemoryArtifactGatewayStore(),
        messages,
        Sandbox(),
        OpenTodos("Membuat dan memvalidasi workbook"),
    )

    result = await finalizer.complete_chat({"taskId": value.id, "text": "Saya akan mengekspor ke Excel."})

    canonical = await messages.load_canonical(
        SessionPartition(
            tenant_id=value.tenant_id,
            owner_object_id=value.owner_object_id,
            session_id=value.session_id,
        ),
        [result["finalMessageId"]],
    )
    text = canonical[0].text
    # Otherwise the user reads a promise, sees "Analysis completed", and gets no file.
    assert "Saya akan mengekspor ke Excel." in text
    assert "stopped before finishing its own plan" in text
    assert "Membuat dan memvalidasi workbook" in text


@pytest.mark.asyncio
async def test_a_run_that_finished_its_plan_is_left_alone() -> None:
    runtime = InMemoryRuntimeStateRepository()
    value = task().model_copy(update={"status": TaskStatus.ANALYZING})
    await runtime.create_task(value, "request-chat-32345678")
    messages = InMemoryProjectionRepository()
    finalizer = CoreTaskFinalizer(runtime, InMemoryArtifactGatewayStore(), messages, Sandbox(), OpenTodos())

    result = await finalizer.complete_chat({"taskId": value.id, "text": "The answer is four."})

    canonical = await messages.load_canonical(
        SessionPartition(
            tenant_id=value.tenant_id,
            owner_object_id=value.owner_object_id,
            session_id=value.session_id,
        ),
        [result["finalMessageId"]],
    )
    assert canonical[0].text == "The answer is four."
