from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

CommandRunner = Callable[[Sequence[str]], list[dict[str, Any]]]


@dataclass(frozen=True, order=True)
class Assignment:
    principal_id: str
    scope: str
    role_definition_id: str


@dataclass(frozen=True, order=True)
class RedisAccessPolicy:
    principal_id: str
    scope: str
    assignment_name: str
    access_policy_name: str


@dataclass(frozen=True)
class ExpectedAssignments:
    arm: frozenset[Assignment]
    cosmos_sql: frozenset[Assignment]
    redis_access_policies: frozenset[RedisAccessPolicy]


def require_string(mapping: Mapping[str, Any], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"expected non-empty string field: {key}")
    return value


def load_json_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return cast(dict[str, Any], value)


def load_deployment_outputs(path: Path) -> dict[str, str]:
    raw_outputs = load_json_object(path)
    outputs: dict[str, str] = {}
    for name, definition in raw_outputs.items():
        if not isinstance(definition, Mapping):
            raise ValueError("deployment outputs must map names to output definitions")
        output_definition = cast(Mapping[str, Any], definition)
        value = output_definition.get("value")
        if not isinstance(value, str) or not value:
            raise ValueError(f"output {name} must have a non-empty string value")
        outputs[name] = value
    return outputs


def assignment_from_definition(
    definition: Mapping[str, Any], outputs: Mapping[str, str], *, require_scope: bool
) -> Assignment:
    principal_output = require_string(definition, "principalOutput")
    role_definition_id = require_string(definition, "roleDefinitionId")
    principal_id = outputs.get(principal_output)
    if principal_id is None:
        raise ValueError(f"missing deployment output: {principal_output}")

    if require_scope:
        scope_output = require_string(definition, "scopeOutput")
        scope = outputs.get(scope_output)
        if scope is None:
            raise ValueError(f"missing deployment output: {scope_output}")
    else:
        scope = require_string(definition, "scope")

    return Assignment(principal_id, scope.lower(), role_definition_id.lower())


def assignment_set(definitions: object, outputs: Mapping[str, str], *, require_scope: bool) -> frozenset[Assignment]:
    if not isinstance(definitions, list):
        raise ValueError("expected assignment list")
    assignments: set[Assignment] = set()
    for definition in cast(list[object], definitions):
        if not isinstance(definition, Mapping):
            raise ValueError("assignment definitions must be objects")
        assignment = assignment_from_definition(
            cast(Mapping[str, Any], definition), outputs, require_scope=require_scope
        )
        if assignment in assignments:
            raise ValueError(f"duplicate expected assignment: {assignment}")
        assignments.add(assignment)
    return frozenset(assignments)


def redis_access_policy_set(definitions: object, outputs: Mapping[str, str]) -> frozenset[RedisAccessPolicy]:
    if not isinstance(definitions, list):
        raise ValueError("expected Redis access-policy list")
    policies: set[RedisAccessPolicy] = set()
    for definition in cast(list[object], definitions):
        if not isinstance(definition, Mapping):
            raise ValueError("Redis access-policy definitions must be objects")
        definition_mapping = cast(Mapping[str, Any], definition)
        principal_output = require_string(definition_mapping, "principalOutput")
        scope_output = require_string(definition_mapping, "scopeOutput")
        principal_id = outputs.get(principal_output)
        scope = outputs.get(scope_output)
        if principal_id is None:
            raise ValueError(f"missing deployment output: {principal_output}")
        if scope is None:
            raise ValueError(f"missing deployment output: {scope_output}")
        assignment_name = definition_mapping.get("assignmentName")
        assignment_prefix = definition_mapping.get("assignmentNamePrefix")
        if isinstance(assignment_prefix, str) and assignment_prefix:
            if assignment_name is not None:
                raise ValueError("Redis assignment must use a name or prefix, not both")
            assignment_name = f"{assignment_prefix}-{principal_id.replace('-', '')[:12]}"
        if not isinstance(assignment_name, str) or not assignment_name:
            raise ValueError("Redis access policy requires an assignment name or prefix")
        policy = RedisAccessPolicy(
            principal_id=principal_id,
            scope=scope,
            assignment_name=assignment_name,
            access_policy_name=require_string(definition_mapping, "accessPolicyName"),
        )
        if policy in policies:
            raise ValueError(f"duplicate expected Redis access policy: {policy}")
        policies.add(policy)
    return frozenset(policies)


def load_expected_assignments(path: Path, outputs: Mapping[str, str]) -> ExpectedAssignments:
    raw_expected = load_json_object(path)
    return ExpectedAssignments(
        arm=assignment_set(raw_expected.get("arm"), outputs, require_scope=True),
        cosmos_sql=assignment_set(raw_expected.get("cosmosSql"), outputs, require_scope=False),
        redis_access_policies=redis_access_policy_set(raw_expected.get("redisAccessPolicies"), outputs),
    )


def run_az_json(arguments: Sequence[str]) -> list[dict[str, Any]]:
    completed = subprocess.run(  # noqa: S603
        ["az", *arguments, "--only-show-errors", "--output", "json"],  # noqa: S607
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        details = completed.stderr.strip() or completed.stdout.strip() or "Azure CLI command failed"
        raise RuntimeError(details)
    value: object = json.loads(completed.stdout)
    if not isinstance(value, list):
        raise ValueError("Azure CLI must return a JSON array")
    records: list[dict[str, Any]] = []
    for item in cast(list[object], value):
        if not isinstance(item, dict):
            raise ValueError("Azure CLI must return a JSON array")
        records.append(cast(dict[str, Any], item))
    return records


def normalized_assignments(
    records: Sequence[Mapping[str, Any]],
    *,
    root_scope: str | None = None,
) -> frozenset[Assignment]:
    assignments: set[Assignment] = set()
    normalized_root = root_scope.lower() if root_scope is not None else None
    for record in records:
        scope = require_string(record, "scope").lower()
        if normalized_root is not None and scope == normalized_root:
            scope = "/"
        assignments.add(
            Assignment(
                principal_id=require_string(record, "principalId"),
                scope=scope,
                role_definition_id=require_string(record, "roleDefinitionId").lower().rsplit("/", 1)[-1],
            )
        )
    return frozenset(assignments)


def normalized_redis_access_policies(records: Sequence[Mapping[str, Any]], scope: str) -> frozenset[RedisAccessPolicy]:
    policies: set[RedisAccessPolicy] = set()
    for record in records:
        properties = record.get("properties")
        if not isinstance(properties, Mapping):
            raise ValueError("Redis access-policy record must contain properties")
        properties_mapping = cast(Mapping[str, Any], properties)
        user = properties_mapping.get("user")
        if not isinstance(user, Mapping):
            raise ValueError("Redis access-policy record must contain a user")
        user_mapping = cast(Mapping[str, Any], user)
        policies.add(
            RedisAccessPolicy(
                principal_id=require_string(user_mapping, "objectId"),
                scope=scope,
                assignment_name=require_string(record, "name"),
                access_policy_name=require_string(properties_mapping, "accessPolicyName"),
            )
        )
    return frozenset(policies)


def resource_group_name(resource_id: str) -> str:
    segments = resource_id.strip("/").split("/")
    for index, segment in enumerate(segments[:-1]):
        if segment.lower() == "resourcegroups":
            return segments[index + 1]
    raise ValueError(f"resource ID has no resource group: {resource_id}")


def resource_name(resource_id: str) -> str:
    name = resource_id.rstrip("/").rsplit("/", 1)[-1]
    if not name:
        raise ValueError(f"resource ID has no resource name: {resource_id}")
    return name


def assert_exact(label: str, expected: frozenset[Assignment], actual: frozenset[Assignment]) -> None:
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing or extra:
        raise ValueError(f"{label} assignments differ; missing={missing}; extra={extra}")


def application_assignments(
    assignments: frozenset[Assignment], expected: frozenset[Assignment]
) -> frozenset[Assignment]:
    application_principal_ids = {assignment.principal_id for assignment in expected}
    return frozenset(assignment for assignment in assignments if assignment.principal_id in application_principal_ids)


def application_redis_access_policies(
    policies: frozenset[RedisAccessPolicy], expected: frozenset[RedisAccessPolicy]
) -> frozenset[RedisAccessPolicy]:
    application_principal_ids = {policy.principal_id for policy in expected}
    return frozenset(policy for policy in policies if policy.principal_id in application_principal_ids)


def verify(outputs: Mapping[str, str], expected: ExpectedAssignments, runner: CommandRunner = run_az_json) -> None:
    resource_ids = [outputs["storageAccountId"], outputs["cosmosAccountId"]]
    graph_query = "Resources | where id in~ ({}) | project id".format(
        ", ".join(f"'{resource_id}'" for resource_id in resource_ids)
    )
    runner(
        [
            "graph",
            "query",
            "--graph-query",
            graph_query,
            "--query",
            "data",
        ]
    )

    actual_arm = application_assignments(
        normalized_assignments(runner(["role", "assignment", "list", "--scope", outputs["storageAccountId"]])),
        expected.arm,
    )
    assert_exact("ARM", expected.arm, actual_arm)

    resource_group = resource_group_name(outputs["cosmosAccountId"])
    cosmos_account = resource_name(outputs["cosmosAccountId"])
    actual_cosmos = application_assignments(
        normalized_assignments(
            runner(
                [
                    "cosmosdb",
                    "sql",
                    "role",
                    "assignment",
                    "list",
                    "--account-name",
                    cosmos_account,
                    "--resource-group",
                    resource_group,
                ]
            ),
            root_scope=outputs["cosmosAccountId"],
        ),
        expected.cosmos_sql,
    )
    assert_exact("Cosmos SQL", expected.cosmos_sql, actual_cosmos)

    if expected.redis_access_policies:
        redis_cluster_id = outputs["redisClusterId"]
        actual_redis = application_redis_access_policies(
            normalized_redis_access_policies(
                runner(
                    [
                        "rest",
                        "--method",
                        "GET",
                        "--url",
                        "https://management.azure.com"
                        f"{redis_cluster_id}/databases/default/accessPolicyAssignments?api-version=2025-07-01",
                        "--query",
                        "value",
                    ]
                ),
                redis_cluster_id,
            ),
            expected.redis_access_policies,
        )
        missing = sorted(expected.redis_access_policies - actual_redis)
        extra = sorted(actual_redis - expected.redis_access_policies)
        if missing or extra:
            raise ValueError(f"Redis access-policy assignments differ; missing={missing}; extra={extra}")


def parse_arguments(arguments: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fail closed on unexpected Core Data Pack RBAC assignments.")
    parser.add_argument("--outputs", type=Path, required=True, help="Azure deployment outputs JSON.")
    parser.add_argument(
        "--expected",
        type=Path,
        default=Path(__file__).with_name("rbac-assignments.json"),
        help="Committed exact RBAC assignment inventory.",
    )
    return parser.parse_args(arguments)


def main(arguments: Sequence[str] | None = None) -> int:
    parsed = parse_arguments(sys.argv[1:] if arguments is None else arguments)
    try:
        outputs = load_deployment_outputs(parsed.outputs)
        expected = load_expected_assignments(parsed.expected, outputs)
        verify(outputs, expected)
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as error:
        print(f"FAIL: {error}")
        return 1

    print("PASS: exact RBAC assignment inventory matches")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
