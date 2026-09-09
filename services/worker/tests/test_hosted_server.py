from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from agent_framework import AgentResponse, Content, Message, WorkflowAgent
from eda_contracts.tasks import TaskStatus
from eda_worker.contracts import ControlSnapshot
from eda_worker.hosted_server import (
    AnalysisAgentResponseError,
    HostedAnalysisServices,
    HostedRuntimeProvider,
    _response_text,
    create_hosted_server,
)


class FakeAgent:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def run(self, message: str, **kwargs: object) -> AgentResponse:
        self.calls.append({"message": message, **kwargs})
        return AgentResponse(messages=[Message(role="assistant", contents=["final answer"])])


class FakeRepository:
    def __init__(self, task: object) -> None:
        self.task_record = task

    async def resolve_task(self, task_id: str) -> object:
        assert task_id == self.task_record.id
        return self.task_record


class FakeActivities:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.validation_outcome = "passed"

    async def validate_outputs(self, payload: dict[str, object]) -> dict[str, str]:
        self.calls.append(("validate", payload))
        return {"outcome": self.validation_outcome, "reportRef": "artifact-set:test"}

    async def load_task_controls(self, payload: dict[str, object]) -> dict[str, object]:
        self.calls.append(("load", payload))
        return {
            "cancellationRequested": False,
            "pendingCommandIds": ["cmd_12345678"],
            "highestCommandSequence": 1,
            "authResumed": False,
        }

    async def checkpoint_task(self, payload: dict[str, object]) -> dict[str, str]:
        self.calls.append(("checkpoint", payload))
        return {"status": str(payload["status"]), "checkpointSequence": "1"}

    async def acknowledge_task_commands(self, payload: dict[str, object]) -> dict[str, str]:
        self.calls.append(("acknowledge", payload))
        return {"outcome": "ok"}

    async def complete_chat(self, payload: dict[str, object]) -> dict[str, str]:
        self.calls.append(("complete", payload))
        return {"finalMessageId": "msg_final123"}

    async def cancel_task(self, payload: dict[str, object]) -> dict[str, str]:
        self.calls.append(("cancel", payload))
        return {"outcome": "cancelled"}

    async def fail_task(self, payload: dict[str, object]) -> dict[str, str]:
        self.calls.append(("fail", payload))
        return {"outcome": "failed"}


class FakeResources:
    def __init__(self) -> None:
        self.closed = 0

    async def aclose(self) -> None:
        self.closed += 1


def test_error_only_agent_response_reports_safe_diagnostics() -> None:
    response = AgentResponse(
        messages=[
            Message(
                role="assistant",
                contents=[
                    Content.from_error(
                        message="ChatClientException: request returned 403 Forbidden with secret-prompt-value",
                        error_code="authorization_failed",
                        error_details="Bearer secret-token-value",
                    )
                ],
            )
        ]
    )

    with pytest.raises(AnalysisAgentResponseError) as captured:
        _response_text(response)

    diagnostic = str(captured.value)
    assert "authorization_failed" in diagnostic
    assert "403" in diagnostic
    assert "ChatClientException" in diagnostic
    assert "secret-prompt-value" not in diagnostic
    assert "secret-token-value" not in diagnostic
    assert captured.value.failure_code == (
        "analysis_failed_agent_response_authorization_failed_http_403_chatclientexception"
    )


@pytest.mark.asyncio
async def test_runtime_provider_builds_once_on_the_serving_loop_and_closes_once(task_record: object) -> None:
    builds = 0
    resources = FakeResources()
    runtime = SimpleNamespace(resources=resources)

    async def factory(settings: object) -> object:
        nonlocal builds
        del settings
        builds += 1
        return runtime

    provider = HostedRuntimeProvider(object(), runtime_factory=factory)

    assert await provider.get() is runtime
    assert await provider.get() is runtime
    await provider.close()
    await provider.close()

    assert builds == 1
    assert resources.closed == 1


@pytest.mark.asyncio
async def test_hosted_services_preserve_trusted_task_scope_and_bounded_agent_options(task_record: object) -> None:
    agent = FakeAgent()
    activities = FakeActivities()
    planner = SimpleNamespace(ensure=AsyncMock(return_value=task_record))
    runtime = SimpleNamespace(
        repository=FakeRepository(task_record),
        output_planner=planner,
        primary_agent=agent,
        activities=activities,
        resources=FakeResources(),
    )

    async def factory(settings: object) -> object:
        del settings
        return runtime

    services = HostedAnalysisServices(HostedRuntimeProvider(object(), runtime_factory=factory))

    controls = await services.controls(task_record.id)
    text = await services.run_analysis(task_record.id, controls.pending_command_ids)
    await services.acknowledge(task_record.id, controls.highest_command_sequence)
    final_message_id = await services.complete(task_record.id, text)

    assert controls == ControlSnapshot(
        cancellationRequested=False,
        pendingCommandIds=("cmd_12345678",),
        highestCommandSequence=1,
        authResumed=False,
    )
    assert text == "final answer"
    assert final_message_id == "msg_final123"
    assert agent.calls[0]["options"] == {
        "task_id": task_record.id,
        "phase": "chat",
        "work_class": "analysis",
        "pending_command_ids": ["cmd_12345678"],
    }
    assert agent.calls[0]["stream"] is False
    assert "Do not add unrequested formats" in agent.calls[0]["message"]
    planner.ensure.assert_awaited_once_with(task_record.id, pending_command_ids=controls.pending_command_ids)


@pytest.mark.asyncio
async def test_hosted_completion_refuses_text_without_validated_requested_outputs(task_record: object) -> None:
    activities = FakeActivities()
    activities.validation_outcome = "failed"

    async def factory(settings: object) -> object:
        return SimpleNamespace(activities=activities, resources=FakeResources())

    services = HostedAnalysisServices(HostedRuntimeProvider(object(), runtime_factory=factory))
    with pytest.raises(ValueError, match="validated requested outputs"):
        await services.complete(task_record.id, "All work is blocked; no artifact is ready.")

    assert activities.calls == [
        ("validate", {"taskId": task_record.id, "requireOutputContract": True}),
    ]


@pytest.mark.asyncio
async def test_hosted_completion_finishes_an_xlsx_only_task_and_releases_sandbox(task_record: object) -> None:
    from eda_contracts import ArtifactKind
    from eda_runtime_state.models import TaskRecord
    from eda_runtime_state.tasks import InMemoryRuntimeStateRepository
    from eda_worker.finalization import CoreTaskFinalizer
    from eda_worker.history.repository import InMemoryProjectionRepository
    from eda_worker.sandbox.gateway import InMemoryArtifactGatewayStore
    from eda_worker.tools.contracts import ValidationProfile

    task = TaskRecord.model_validate({**task_record.model_dump(), "requiredOutputs": [{"kind": "xlsx"}]})
    repository = InMemoryRuntimeStateRepository()
    await repository.create_task(task, "request-xlsx-only-12345678")
    artifacts = InMemoryArtifactGatewayStore()
    candidate = await artifacts.persist_bytes(task.id, ArtifactKind.XLSX, "analysis.xlsx", b"workbook")
    report = await artifacts.persist_validation(task.id, candidate, ValidationProfile.CORE_XLSX, b"passed", "passed")
    await artifacts.publish(task.id, candidate, report)
    sandbox = SimpleNamespace(stop_task=AsyncMock())
    finalizer = CoreTaskFinalizer(repository, artifacts, InMemoryProjectionRepository(), sandbox)

    async def factory(settings: object) -> object:
        return SimpleNamespace(activities=finalizer, resources=FakeResources())

    services = HostedAnalysisServices(HostedRuntimeProvider(object(), runtime_factory=factory))

    message_id = await services.complete(task.id, "Excel is ready.")

    completed = await repository.resolve_task(task.id)
    assert completed is not None and completed.final_message_id == message_id
    sandbox.stop_task.assert_awaited_once_with(task.id)


def test_server_wraps_a_graph_workflow_with_resilient_background_enabled() -> None:
    server = create_hosted_server(object(), runtime_factory=lambda settings: None)  # type: ignore[arg-type]

    assert isinstance(server._agent, WorkflowAgent)  # pyright: ignore[reportPrivateUsage]
    assert server._resilient_background is True  # pyright: ignore[reportPrivateUsage]


def test_analysis_runtime_separates_agent_services_from_the_legacy_dts_worker(task_record: object) -> None:
    from eda_worker.runtime import AnalysisRuntime

    runtime = AnalysisRuntime(
        repository=FakeRepository(task_record),
        primary_agent=FakeAgent(),
        activities=FakeActivities(),
        resources=FakeResources(),
        output_planner=SimpleNamespace(ensure=AsyncMock(return_value=task_record)),
    )

    assert runtime.repository.task_record is task_record
    assert isinstance(runtime.primary_agent, FakeAgent)


def test_worker_settings_use_a_first_class_sandbox_group_without_dts_or_session_pool() -> None:
    from eda_worker.config import WorkerSettings

    settings = WorkerSettings.model_validate(
        {
            "managed_identity_client_id": "11111111-1111-1111-1111-111111111111",
            "entra_client_id": "22222222-2222-2222-2222-222222222222",
            "cosmos_endpoint": "https://example.documents.azure.com",
            "blob_account_url": "https://example.blob.core.windows.net",
            "redis_url": "redis://localhost:6379",
            "foundry_project_endpoint": "https://example.services.ai.azure.com/api/projects/example",
            "foundry_model_deployment": "gpt-5.6-terra",
            "sandbox_subscription_id": "33333333-3333-3333-3333-333333333333",
            "sandbox_resource_group": "rg-eda-demo",
            "sandbox_group": "sbg-eda-demo",
            "sandbox_region": "southeastasia",
            "sandbox_disk_image_id": "disk_12345678",
        }
    )

    assert not hasattr(settings, "dts_endpoint")
    assert not hasattr(settings, "dts_taskhub")
    assert settings.sandbox_group == "sbg-eda-demo"
    assert not hasattr(settings, "session_pool_management_endpoint")


@pytest.fixture
def task_record() -> Any:
    from datetime import UTC, datetime, timedelta
    from uuid import UUID

    from eda_runtime_state.models import TaskRecord

    now = datetime.now(UTC)
    return TaskRecord(
        id="task_12345678",
        tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
        owner_object_id=UUID("22222222-2222-2222-2222-222222222222"),
        session_id="ses_1234567890abcdef",
        status=TaskStatus.ANALYZING,
        checkpoint_sequence=1,
        command_sequence=1,
        applied_command_sequence=0,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(days=1),
    )
