from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/verify-rbac.py"
EXPECTED = ROOT / "scripts/rbac-assignments.json"


def load_script() -> ModuleType:
    specification = importlib.util.spec_from_file_location("verify_rbac_under_test", SCRIPT)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


def test_hosted_redis_assignment_name_is_bound_to_the_current_principal() -> None:
    module = load_script()
    outputs = {
        "webIdentityPrincipalId": "11111111-1111-1111-1111-111111111111",
        "workerIdentityPrincipalId": "22222222-2222-2222-2222-222222222222",
        "hostedAgentPrincipalId": "33333333-3333-3333-3333-333333333333",
        "storageAccountId": "/subscriptions/s/resourceGroups/r/providers/Microsoft.Storage/storageAccounts/a",
        "cosmosAccountId": "/subscriptions/s/resourceGroups/r/providers/Microsoft.DocumentDB/databaseAccounts/c",
        "redisClusterId": "/subscriptions/s/resourceGroups/r/providers/Microsoft.Cache/redisEnterprise/cache",
    }

    expected = module.load_expected_assignments(EXPECTED, outputs)
    hosted = next(
        policy for policy in expected.redis_access_policies if policy.principal_id == outputs["hostedAgentPrincipalId"]
    )

    assert hosted.assignment_name == "hostedAgent-333333333333"


def test_scoped_arm_assignment_query_does_not_use_all() -> None:
    module = load_script()
    calls: list[tuple[str, ...]] = []
    outputs = {
        "storageAccountId": "/subscriptions/s/resourceGroups/r/providers/Microsoft.Storage/storageAccounts/a",
        "cosmosAccountId": "/subscriptions/s/resourceGroups/r/providers/Microsoft.DocumentDB/databaseAccounts/c",
    }
    expected = module.ExpectedAssignments(
        arm=frozenset(),
        cosmos_sql=frozenset(),
        redis_access_policies=frozenset(),
    )

    def runner(command: list[str]) -> list[dict[str, object]]:
        calls.append(tuple(command))
        return []

    module.verify(outputs, expected, runner=runner)

    role_query = next(command for command in calls if command[:3] == ("role", "assignment", "list"))
    assert "--scope" in role_query
    assert "--all" not in role_query


def test_arm_assignment_scope_comparison_is_case_insensitive() -> None:
    module = load_script()
    principal = "11111111-1111-1111-1111-111111111111"
    role = "ba92f5b4-2d11-453d-a403-e96b0029c9fe"
    scope = "/subscriptions/s/resourceGroups/r/providers/Microsoft.Storage/storageAccounts/a"
    expected = module.assignment_from_definition(
        {
            "principalOutput": "principal",
            "roleDefinitionId": role,
            "scopeOutput": "scope",
        },
        {"principal": principal, "scope": scope},
        require_scope=True,
    )

    actual = module.normalized_assignments(
        [
            {
                "principalId": principal,
                "roleDefinitionId": f"/providers/Microsoft.Authorization/roleDefinitions/{role}",
                "scope": scope.replace("resourceGroups", "resourcegroups"),
            }
        ]
    )

    assert actual == frozenset({expected})


def test_cosmos_account_id_is_equivalent_to_root_scope() -> None:
    module = load_script()
    principal = "11111111-1111-1111-1111-111111111111"
    role = "00000000-0000-0000-0000-000000000002"
    account = "/subscriptions/s/resourceGroups/r/providers/Microsoft.DocumentDB/databaseAccounts/c"
    expected = module.Assignment(principal, "/", role)

    actual = module.normalized_assignments(
        [
            {
                "principalId": principal,
                "roleDefinitionId": role,
                "scope": account,
            }
        ],
        root_scope=account,
    )

    assert actual == frozenset({expected})
