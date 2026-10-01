"""One execution ledger bounds capacity, ownership and conversation exclusivity (spec A16, A25, A26).

Every property is checked through the Cosmos store over an ETag-faithful
container, including simultaneous claims from several replicas.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from azure.cosmos.exceptions import CosmosHttpResponseError
from eda_runtime_state.ledger import ExecutionLedgerStore, InMemoryLedgerContainer, LeaseConflict

TTL = timedelta(seconds=45)


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 9, 23, 8, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def _store(container: InMemoryLedgerContainer | None = None, clock: Clock | None = None) -> ExecutionLedgerStore:
    return ExecutionLedgerStore(container or InMemoryLedgerContainer(), clock=clock or Clock())


def _task(index: int) -> str:
    return f"task_{index:08d}abcdef"


def _session(index: int) -> str:
    return f"ses_{index:08d}abcdefgh"


@pytest.mark.asyncio
async def test_capacity_is_bounded_across_replicas() -> None:
    container = InMemoryLedgerContainer()
    first, second = _store(container), _store(container)

    outcomes = [
        (await first.claim(_task(index), _session(index), "replica-a", ttl=TTL, limit=5))[0] for index in range(3)
    ]
    outcomes += [
        (await second.claim(_task(index), _session(index), "replica-b", ttl=TTL, limit=5))[0] for index in range(3, 7)
    ]

    assert outcomes == ["claimed"] * 5 + ["capacity_full"] * 2
    assert len((await first.snapshot()).entries) == 5


@pytest.mark.asyncio
async def test_simultaneous_claims_never_exceed_the_limit() -> None:
    container = InMemoryLedgerContainer()
    stores = [_store(container) for _ in range(8)]

    results = await asyncio.gather(
        *(
            store.claim(_task(index), _session(index), f"replica-{index}", ttl=TTL, limit=5)
            for index, store in enumerate(stores)
        )
    )

    assert [outcome for outcome, _ in results].count("claimed") == 5
    assert len((await stores[0].snapshot()).entries) == 5


@pytest.mark.asyncio
async def test_a_conversation_never_executes_two_tasks_even_on_two_replicas() -> None:
    container = InMemoryLedgerContainer()
    first, second = _store(container), _store(container)

    outcome_a, _ = await first.claim(_task(1), _session(1), "replica-a", ttl=TTL, limit=5)
    outcome_b, _ = await second.claim(_task(2), _session(1), "replica-b", ttl=TTL, limit=5)

    assert (outcome_a, outcome_b) == ("claimed", "conversation_busy")


@pytest.mark.asyncio
async def test_simultaneous_claims_for_one_conversation_admit_exactly_one_task() -> None:
    container = InMemoryLedgerContainer()
    stores = [_store(container) for _ in range(4)]

    results = await asyncio.gather(
        *(
            store.claim(_task(index), _session(7), f"replica-{index}", ttl=TTL, limit=5)
            for index, store in enumerate(stores)
        )
    )

    assert [outcome for outcome, _ in results].count("claimed") == 1


@pytest.mark.asyncio
async def test_a_task_has_one_owner_and_a_second_claimant_is_refused() -> None:
    container = InMemoryLedgerContainer()
    first, second = _store(container), _store(container)

    outcome, claim = await first.claim(_task(1), _session(1), "replica-a", ttl=TTL, limit=5)
    refused, nothing = await second.claim(_task(1), _session(1), "replica-b", ttl=TTL, limit=5)

    assert outcome == "claimed" and claim is not None
    assert (refused, nothing) == ("owned_elsewhere", None)


@pytest.mark.asyncio
async def test_an_expired_entry_is_adopted_by_recovery_with_a_new_fence() -> None:
    clock = Clock()
    container = InMemoryLedgerContainer()
    crashed, recovering = _store(container, clock), _store(container, clock)
    _, old = await crashed.claim(_task(1), _session(1), "old-revision", ttl=TTL, limit=5)
    assert old is not None

    clock.advance(46)
    outcome, new = await recovering.claim(_task(1), _session(1), "new-revision", ttl=TTL, limit=5)

    assert outcome == "claimed" and new is not None
    assert new.fence != old.fence
    assert not await crashed.is_held(old)
    assert await recovering.is_held(new)
    # The stale owner cannot extend or release what is now someone else's.
    assert await crashed.renew({old.task_id: old.fence}, ttl=TTL) == set()
    await crashed.release(old)
    assert await recovering.is_held(new)


@pytest.mark.asyncio
async def test_a_reclaim_by_the_same_owner_fences_its_earlier_coroutine() -> None:
    store = _store()
    _, earlier = await store.claim(_task(1), _session(1), "replica-a", ttl=TTL, limit=5)
    _, later = await store.claim(_task(1), _session(1), "replica-a", ttl=TTL, limit=5)

    assert earlier is not None and later is not None
    assert earlier.fence != later.fence
    assert not await store.is_held(earlier)
    assert len((await store.snapshot()).entries) == 1


@pytest.mark.asyncio
async def test_orphaned_entries_expire_instead_of_blocking_capacity_forever() -> None:
    clock = Clock()
    store = _store(clock=clock)
    for index in range(5):
        await store.claim(_task(index), _session(index), "crashed-replica", ttl=TTL, limit=5)
    assert (await store.claim(_task(9), _session(9), "replica-b", ttl=TTL, limit=5))[0] == "capacity_full"

    clock.advance(46)

    assert (await store.claim(_task(9), _session(9), "replica-b", ttl=TTL, limit=5))[0] == "claimed"
    assert [entry.task_id for entry in (await store.snapshot()).entries] == [_task(9)]


@pytest.mark.asyncio
async def test_renewal_extends_only_entries_whose_fence_still_matches() -> None:
    clock = Clock()
    store = _store(clock=clock)
    _, first = await store.claim(_task(1), _session(1), "replica-a", ttl=TTL, limit=5)
    _, second = await store.claim(_task(2), _session(2), "replica-a", ttl=TTL, limit=5)
    _, other = await store.claim(_task(3), _session(3), "replica-b", ttl=TTL, limit=5)
    assert first and second and other

    for _ in range(4):
        clock.advance(30)
        held = await store.renew({first.task_id: first.fence, second.task_id: second.fence}, ttl=TTL)
        assert held == {first.task_id, second.task_id}

    live = {entry.task_id for entry in (await store.snapshot()).live(clock.now)}
    # replica-b never renewed, so only replica-a's two tasks remain.
    assert live == {first.task_id, second.task_id}


@pytest.mark.asyncio
async def test_release_frees_capacity_and_the_conversation() -> None:
    store = _store()
    _, claim = await store.claim(_task(1), _session(1), "replica-a", ttl=TTL, limit=1)
    assert claim is not None
    assert (await store.claim(_task(2), _session(1), "replica-a", ttl=TTL, limit=1))[0] == "conversation_busy"

    await store.release(claim)

    assert (await store.claim(_task(2), _session(1), "replica-a", ttl=TTL, limit=1))[0] == "claimed"


@pytest.mark.asyncio
async def test_a_ledger_that_keeps_changing_raises_rather_than_claiming_success() -> None:
    class AlwaysStale(InMemoryLedgerContainer):
        async def replace_item(self, item, body, *, etag, match_condition):  # type: ignore[override]
            raise CosmosHttpResponseError(status_code=412, message="precondition failed")

    store = _store(AlwaysStale())

    with pytest.raises(LeaseConflict):
        await store.claim(_task(1), _session(1), "replica-a", ttl=TTL, limit=5)
