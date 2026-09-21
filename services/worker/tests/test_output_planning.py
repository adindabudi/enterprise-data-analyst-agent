from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from agent_framework import ChatResponse, Message
from eda_contracts import ArtifactKind
from eda_contracts.controls import CommandKind
from eda_contracts.tasks import TaskStatus
from eda_runtime_state.models import TaskRecord
from eda_runtime_state.tasks import InMemoryRuntimeStateRepository


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("user_request", "kinds"),
    [
        ("Ekspor hasil query ke Excel saja, tidak perlu dashboard.", (ArtifactKind.XLSX,)),
        ("Analisis CSV dan buat workbook Excel.", (ArtifactKind.XLSX,)),
        ("Buat dashboard HTML.", (ArtifactKind.HTML,)),
        ("Buat dashboard HTML dan workbook Excel.", (ArtifactKind.HTML, ArtifactKind.XLSX)),
        ("Jelaskan hasil analisis tanpa file unduhan.", ()),
    ],
)
async def test_output_planner_persists_contract_without_tools_and_reuses_it(
    user_request: str,
    kinds: tuple[ArtifactKind, ...],
) -> None:
    from eda_worker.output_planning import OutputContractPlanner

    now = datetime.now(UTC)
    task = TaskRecord(
        id="task_12345678",
        tenant_id=UUID(int=1),
        owner_object_id=UUID(int=2),
        session_id="ses_12345678",
        status=TaskStatus.ANALYZING,
        checkpoint_sequence=0,
        command_sequence=0,
        applied_command_sequence=0,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(days=1),
    )
    repository = InMemoryRuntimeStateRepository()
    await repository.create_task(task, "request-12345678")
    client = SimpleNamespace(
        get_response=AsyncMock(
            return_value=ChatResponse(
                messages=[
                    Message(
                        role="assistant",
                        contents=[
                            json.dumps(
                                {
                                    "outputs": [{"kind": kind.value, "minimumCount": 1} for kind in kinds],
                                }
                            )
                        ],
                    ),
                ]
            )
        )
    )
    context = SimpleNamespace(
        context_snapshot=AsyncMock(
            return_value=SimpleNamespace(
                confirmed_requirements=(user_request,),
            )
        )
    )
    planner = OutputContractPlanner(client, repository, context, model_options={"max_tokens": 4096})

    first = await planner.ensure(task.id)
    replay = await planner.ensure(task.id)

    assert first == replay
    assert first.required_outputs is not None
    assert tuple(output.kind for output in first.required_outputs) == kinds
    client.get_response.assert_awaited_once()
    options = client.get_response.call_args.kwargs["options"]
    assert options["tools"] == []
    assert options["store"] is False
    assert "response_format" in options

    command = await repository.append_command(
        task.partition(),
        task.id,
        CommandKind.STEER,
        "Tidak perlu workbook, HTML saja.",
        "steer-12345678",
    )
    client.get_response.return_value = ChatResponse(
        messages=[
            Message(role="assistant", contents=['{"outputs":[{"kind":"html","minimumCount":1}]}']),
        ]
    )
    revised = await planner.ensure(task.id, pending_command_ids=(command.id,))
    assert revised.required_outputs is not None
    assert [output.kind for output in revised.required_outputs] == [ArtifactKind.HTML]
    assert revised.required_outputs_sequence == command.sequence
    assert command.text in client.get_response.call_args.args[0][1].text
    assert await planner.ensure(task.id, pending_command_ids=(command.id,)) == revised
    assert client.get_response.await_count == 2


@pytest.mark.parametrize(
    "payload",
    [
        '{"outputs":[{"kind":"input"}]}',
        '{"outputs":[{"kind":"xlsx","minimumCount":0}]}',
        '{"outputs":[{"kind":"xlsx"},{"kind":"xlsx"}]}',
        "not json",
    ],
)
def test_output_contract_rejects_invalid_model_results(payload: str) -> None:
    from eda_worker.output_planning import OutputContract

    with pytest.raises(ValueError):
        OutputContract.model_validate_json(payload)
