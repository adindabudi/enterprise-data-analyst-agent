"""Identical reads inside one task reach the source once.

These cover A03 (a repeated identical query, including two issued at the same
moment, produces one provider execution and shared evidence) and the part of
A04 that forbids reusing a result across a changed query, source, principal or
task.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

import pytest
from eda_api.fabric_auth.query_reuse import TaskQueryCoalescer, query_fingerprint
from eda_runtime_state.models import TaskPartition

TENANT = UUID("11111111-1111-1111-1111-111111111111")
OWNER = UUID("22222222-2222-2222-2222-222222222222")
OTHER_OWNER = UUID("33333333-3333-3333-3333-333333333333")
QUERY = "MATCH (r:rooms) RETURN count(*) AS total"


def _partition(owner: UUID = OWNER, session: str = "ses_interactive_1234") -> TaskPartition:
    return TaskPartition(tenant_id=TENANT, owner_object_id=owner, session_id=session)


def _fingerprint(
    *,
    owner: UUID = OWNER,
    source: str = "lamna",
    route: str = "gql",
    query: str = QUERY,
    options: dict[str, object] | None = None,
) -> str:
    return query_fingerprint(_partition(owner=owner), source=source, route=route, query=query, options=options)


@pytest.mark.asyncio
async def test_the_same_query_twice_in_one_task_reaches_the_source_once() -> None:
    coalescer = TaskQueryCoalescer()
    calls = 0

    async def execute() -> str:
        nonlocal calls
        calls += 1
        return '[{"total":24}]'

    first = await coalescer.run(_fingerprint(), execute)
    second = await coalescer.run(_fingerprint(), execute)

    assert calls == 1
    assert first.rows == second.rows == '[{"total":24}]'
    assert first.reused is False
    assert second.reused is True
    assert (coalescer.executed, coalescer.reused) == (1, 1)


@pytest.mark.asyncio
async def test_two_simultaneous_identical_queries_share_one_execution() -> None:
    coalescer = TaskQueryCoalescer()
    started = 0
    release = asyncio.Event()

    async def execute() -> str:
        nonlocal started
        started += 1
        await release.wait()
        return '[{"total":24}]'

    waiting = [asyncio.create_task(coalescer.run(_fingerprint(), execute)) for _ in range(3)]
    await asyncio.sleep(0)
    release.set()
    outcomes = await asyncio.gather(*waiting)

    assert started == 1
    assert [outcome.rows for outcome in outcomes] == ['[{"total":24}]'] * 3
    assert [outcome.reused for outcome in outcomes].count(False) == 1


@pytest.mark.asyncio
async def test_a_failure_is_never_stored_as_this_tasks_answer() -> None:
    coalescer = TaskQueryCoalescer()
    attempts = 0

    async def execute() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise TimeoutError("the graph did not answer within the deadline")
        return '[{"total":24}]'

    with pytest.raises(TimeoutError):
        await coalescer.run(_fingerprint(), execute)

    # A timeout is an unknown outcome, so the next attempt must actually ask again
    # rather than inherit an empty or failed result.
    outcome = await coalescer.run(_fingerprint(), execute)
    assert outcome.rows == '[{"total":24}]'
    assert outcome.reused is False
    assert attempts == 2


@pytest.mark.asyncio
async def test_a_waiter_sees_the_shared_failure_rather_than_an_empty_result() -> None:
    coalescer = TaskQueryCoalescer()
    release = asyncio.Event()

    async def execute() -> str:
        await release.wait()
        raise TimeoutError("the graph did not answer within the deadline")

    first = asyncio.create_task(coalescer.run(_fingerprint(), execute))
    await asyncio.sleep(0)
    second = asyncio.create_task(coalescer.run(_fingerprint(), execute))
    await asyncio.sleep(0)
    release.set()

    for pending in (first, second):
        with pytest.raises(TimeoutError):
            await pending


@pytest.mark.parametrize(
    "changed",
    [
        {"query": "MATCH (d:departments) RETURN count(*) AS total"},
        {"source": "another-source"},
        {"route": "ontology_search"},
        {"owner": OTHER_OWNER},
        {"options": {"naturalLanguageResponse": True}},
    ],
)
@pytest.mark.asyncio
async def test_a_changed_question_is_a_different_question(changed: dict[str, object]) -> None:
    coalescer = TaskQueryCoalescer()
    calls = 0

    async def execute() -> str:
        nonlocal calls
        calls += 1
        return f"rows-{calls}"

    await coalescer.run(_fingerprint(), execute)
    await coalescer.run(_fingerprint(**changed), execute)  # pyright: ignore[reportArgumentType]

    assert calls == 2


@pytest.mark.asyncio
async def test_a_new_task_fetches_business_values_again() -> None:
    calls = 0

    async def execute() -> str:
        nonlocal calls
        calls += 1
        return f"rows-{calls}"

    # A coalescer belongs to one task, so the next task starts with no stored values.
    await TaskQueryCoalescer().run(_fingerprint(), execute)
    await TaskQueryCoalescer().run(_fingerprint(), execute)

    assert calls == 2


def test_the_fingerprint_keeps_meaning_distinct_and_ignores_only_outer_whitespace() -> None:
    assert _fingerprint(query=f"  {QUERY}  ") == _fingerprint()
    assert _fingerprint(query=QUERY.replace("rooms", "Rooms")) != _fingerprint()
    assert _fingerprint(query=QUERY.replace("count(*)", "count(r)")) != _fingerprint()
