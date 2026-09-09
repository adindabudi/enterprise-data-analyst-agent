from __future__ import annotations

from datetime import UTC, datetime

import pytest
from eda_provenance import ManifestValidationError, TaskManifest, assert_manifest_safe
from pydantic import ValidationError


def valid_manifest() -> dict[str, object]:
    return {
        "schemaVersion": "1.0",
        "taskId": "task_01HZZZZZZZZZZZZZZZZZZZZZZZ",
        "model": {
            "provider": "foundry",
            "modelProfile": "claude-opus-4-8-xhigh-v1",
            "baseModel": "claude-opus-4-8",
            "deployment": "analysis-opus",
            "hosting": "azure",
            "effort": "xhigh",
            "reasoningMode": "adaptive",
            "promptTemplateVersion": "claude-opus-4-8-v1",
            "promptSha256": "c" * 64,
            "requestOptionsSha256": "d" * 64,
            "contextSnapshotVersion": 4,
        },
        "inputs": [{"artifactId": "input-7", "version": 1, "sha256": "a" * 64}],
        "fabricQueries": [],
        "executions": [],
        "checks": {"schemaValid": True, "keyTotalsReconciled": True, "missingValuesReviewed": True},
        "outputs": [{"artifactId": "output-9", "version": 1, "sha256": "b" * 64, "status": "ready"}],
        "createdAt": datetime(2026, 7, 23, tzinfo=UTC).isoformat(),
    }


def test_manifest_matches_versioned_contract() -> None:
    manifest = TaskManifest.model_validate(valid_manifest())
    assert manifest.schema_version == "1.0"
    assert manifest.model.model_profile == "claude-opus-4-8-xhigh-v1"
    assert manifest.model.reasoning_mode == "adaptive"
    assert manifest.model.prompt_sha256 == "c" * 64
    assert manifest.model.request_options_sha256 == "d" * 64
    assert manifest.outputs[0].status == "ready"


def test_fabric_query_public_record_contains_evidence_but_no_owner_partition() -> None:
    body = valid_manifest()
    body["fabricQueries"] = [
        {
            "queryId": "fabric-query-1234567890abcdef",
            "sourceAlias": "sales",
            "purpose": "aggregate",
            "semanticModelId": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "querySha256": "e" * 64,
            "providerSchemaSha256": "f" * 64,
            "resultArtifactId": "artifact-fabric-result-1234",
            "resultSha256": "a" * 64,
            "resultShape": "tabular",
            "rowCount": 1,
            "attempts": 1,
            "reconciliationStatus": "not_required",
            "executedAt": datetime(2026, 7, 23, tzinfo=UTC).isoformat(),
        }
    ]

    manifest = TaskManifest.model_validate(body)
    [record] = manifest.fabric_queries
    assert record.source_alias == "sales"
    assert record.result_sha256 == "a" * 64
    assert not set(record.model_dump(mode="json", by_alias=True)) & {
        "tenantId",
        "ownerObjectId",
        "sessionId",
        "taskId",
    }

    body["fabricQueries"][0]["ownerObjectId"] = "22222222-2222-2222-2222-222222222222"  # type: ignore[index]
    with pytest.raises(ValidationError):
        TaskManifest.model_validate(body)


def test_assert_manifest_safe_returns_a_valid_manifest_on_the_happy_path() -> None:
    manifest = assert_manifest_safe(valid_manifest())
    assert isinstance(manifest, TaskManifest)
    assert manifest.task_id == "task_01HZZZZZZZZZZZZZZZZZZZZZZZ"


@pytest.mark.parametrize(
    "key",
    [
        "access_token",
        "refreshToken",
        "authorization",
        "cookie",
        "clientSecret",
        "access token",
        "access.token",
        "access:token",
        " api_key",
        "api_key ",
        "X-Api-Key",
        "password",
        "database_password",
        "connection_string",
        "private_key",
        "sas_token",
        "session_token",
        "bearer",
        "jwt",
        "credential",
        "credentials",
    ],
)
def test_manifest_rejects_credential_shaped_keys_at_any_depth(key: str) -> None:
    body = valid_manifest()
    body["model"] = {**body["model"], key: "secret"}  # type: ignore[arg-type]
    with pytest.raises(ManifestValidationError, match="credential-shaped key"):
        assert_manifest_safe(body)


def test_manifest_rejects_credential_shaped_keys_inside_list_items() -> None:
    body = valid_manifest()
    body["executions"] = [
        {
            "executionId": "exec-1",
            "scriptArtifactId": "script-1",
            "scriptSha256": "c" * 64,
            "parameters": {"database_password": "hunter2"},
            "runtimeImageDigest": "sha256:" + "d" * 64,
            "exitCode": 0,
        }
    ]
    with pytest.raises(ManifestValidationError, match="credential-shaped key"):
        assert_manifest_safe(body)


def test_manifest_rejects_excessive_nesting_depth_instead_of_crashing() -> None:
    nested: dict[str, object] = {"leaf": "value"}
    for _ in range(200):
        nested = {"child": nested}
    with pytest.raises(ManifestValidationError, match="maximum depth"):
        assert_manifest_safe(nested)


def test_task_manifest_model_validate_directly_also_rejects_credential_shaped_keys() -> None:
    body = valid_manifest()
    body["executions"] = [
        {
            "executionId": "exec-1",
            "scriptArtifactId": "script-1",
            "scriptSha256": "c" * 64,
            "parameters": {"api_key": "secret"},
            "runtimeImageDigest": "sha256:" + "d" * 64,
            "exitCode": 0,
        }
    ]
    with pytest.raises(ValidationError, match="credential-shaped key"):
        TaskManifest.model_validate(body)


def test_manifest_does_not_false_positive_on_benign_field_names() -> None:
    manifest = assert_manifest_safe(valid_manifest())
    assert manifest.model.prompt_template_version == "claude-opus-4-8-v1"
