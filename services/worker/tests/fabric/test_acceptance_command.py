from __future__ import annotations

from datetime import UTC, datetime

import pytest
from eda_contracts import ArtifactKind, ArtifactRef
from eda_worker.acceptance.fabric import (
    FabricAcceptanceInput,
    acceptance_passed,
    run_fabric_acceptance,
    sanitized_result_document,
)
from eda_worker.fabric.contracts import FabricPrincipal, FabricQueryOperation, FabricQueryResult
from eda_worker.fabric.readiness import FabricFeatureRecord


class Gateway:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, FabricQueryOperation]] = []

    async def query(
        self,
        task_id: str,
        invocation_id: str,
        principal: FabricPrincipal,
        operation: FabricQueryOperation,
    ) -> FabricQueryResult:
        del principal
        self.calls.append((task_id, invocation_id, operation))
        if "no_build" in task_id:
            return FabricQueryResult(
                status="authorization_error",
                summary="Fabric access is not authorized for this semantic model.",
                error_code="build_permission_denied",
                retryable=False,
            )
        digest = "a" * 64 if "rls_a" in task_id else "b" * 64
        return FabricQueryResult(
            status="ok",
            summary="Control total matched.",
            query_ref=f"fabric-query-{task_id}",
            artifact_refs=(
                ArtifactRef(
                    artifact_id=f"artifact-{task_id}",
                    version=1,
                    kind=ArtifactKind.DATA,
                    sha256=digest,
                ),
            ),
        )


def principal(owner: str) -> dict[str, str]:
    return {
        "tenant_id": "11111111-1111-1111-1111-111111111111",
        "owner_object_id": owner,
        "audience": "33333333-3333-3333-3333-333333333333",
    }


def acceptance_input() -> FabricAcceptanceInput:
    return FabricAcceptanceInput.model_validate(
        {
            "runId": "run_01HZZZZZZZZZZZZZZZZZZZZZZZ",
            "deploymentId": "deployment-20260724",
            "providerContractSha256": "c" * 64,
            "fixtures": [
                {
                    "label": "rls_a",
                    "principal": principal("22222222-2222-2222-2222-222222222222"),
                    "taskId": "task_rls_a_12345678",
                    "invocationId": "invoke_rls_a_1234",
                    "semanticModel": "sales",
                    "question": "Return the RLS control total.",
                    "expectedResultSha256": "a" * 64,
                },
                {
                    "label": "rls_b",
                    "principal": principal("44444444-4444-4444-4444-444444444444"),
                    "taskId": "task_rls_b_12345678",
                    "invocationId": "invoke_rls_b_1234",
                    "semanticModel": "sales",
                    "question": "Return the RLS control total.",
                    "expectedResultSha256": "b" * 64,
                },
                {
                    "label": "no_build",
                    "principal": principal("55555555-5555-5555-5555-555555555555"),
                    "taskId": "task_no_build_12345678",
                    "invocationId": "invoke_no_build_1234",
                    "semanticModel": "sales",
                    "question": "Return the control total.",
                },
            ],
        }
    )


def feature(**updates: object) -> FabricFeatureRecord:
    values: dict[str, object] = {
        "state": "configured",
        "deploymentId": "deployment-20260724",
        "providerContractSha256": "c" * 64,
        "modelProfile": "gpt-5.6-terra-medium-v1",
        "modelDeployment": "gpt-5.6-terra",
        "servedModel": "gpt-5.6-terra",
        "servedSnapshot": "2026-07-09",
        "promptVersion": "gpt-5.6-terra-v1",
        "promptSha256": "d" * 64,
        "requestOptionsSha256": "e" * 64,
        "verifiedAt": datetime.now(UTC),
    }
    values.update(updates)
    return FabricFeatureRecord.model_validate(values)


@pytest.mark.asyncio
async def test_acceptance_runs_gateway_directly_and_writes_hash_only_result() -> None:
    gateway = Gateway()

    result = await run_fabric_acceptance(
        acceptance_input(),
        feature=feature(),
        gateway=gateway,
        acceptance_mode=True,
    )

    assert acceptance_passed(result) is True
    assert [call[2].purpose.value for call in gateway.calls] == ["control_total"] * 3
    document = sanitized_result_document(result)
    serialized = str(document)
    assert "22222222-2222-2222-2222-222222222222" not in serialized
    assert "Return the RLS control total" not in serialized
    assert set(result.controls.values()) == {"passed"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("acceptance_mode", "feature_updates", "match"),
    [
        (False, {}, "mode"),
        (True, {"state": "ready", "acceptanceEvidenceSha256": "f" * 64}, "configured"),
        (True, {"deploymentId": "another-deployment"}, "deployment"),
        (True, {"providerContractSha256": "f" * 64}, "provider contract"),
    ],
)
async def test_acceptance_rejects_ordinary_worker_and_mismatched_state(
    acceptance_mode: bool,
    feature_updates: dict[str, object],
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        await run_fabric_acceptance(
            acceptance_input(),
            feature=feature(**feature_updates),
            gateway=Gateway(),
            acceptance_mode=acceptance_mode,
        )


def test_acceptance_input_requires_distinct_rls_hashes_and_no_build_without_value() -> None:
    body = acceptance_input().model_dump(mode="json", by_alias=True)
    fixtures = body["fixtures"]
    assert isinstance(fixtures, list)
    fixtures[1]["expectedResultSha256"] = "a" * 64
    with pytest.raises(ValueError, match="distinct"):
        FabricAcceptanceInput.model_validate(body)
