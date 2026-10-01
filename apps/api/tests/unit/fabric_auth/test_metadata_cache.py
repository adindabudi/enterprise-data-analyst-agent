"""The metadata cache keeps one customer's structure away from another's session.

These cover A05 (a second task for the same principal reuses metadata, a
different principal shares none) and the parts of A04 that concern metadata
going stale or being invalidated.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

import pytest
from eda_api.fabric_auth.metadata_cache import MetadataCache, MetadataKey
from eda_runtime_state.models import TaskPartition

TENANT = UUID("11111111-1111-1111-1111-111111111111")
OWNER = UUID("22222222-2222-2222-2222-222222222222")
OTHER_OWNER = UUID("33333333-3333-3333-3333-333333333333")


def _partition(owner: UUID = OWNER, session: str = "ses_first_12345678") -> TaskPartition:
    return TaskPartition(tenant_id=TENANT, owner_object_id=owner, session_id=session)


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@pytest.mark.asyncio
async def test_a_second_task_for_the_same_principal_reuses_metadata_without_rediscovering() -> None:
    cache = MetadataCache()
    loads = 0

    async def load() -> str:
        nonlocal loads
        loads += 1
        return "rooms: RoomId"

    first = MetadataKey.for_principal(_partition(session="ses_first_12345678"), source="ont", kind="schema")
    later = MetadataKey.for_principal(_partition(session="ses_second_1234567"), source="ont", kind="schema")

    assert await cache.get_or_load(first, load) == "rooms: RoomId"
    assert await cache.get_or_load(later, load) == "rooms: RoomId"
    assert loads == 1


@pytest.mark.asyncio
async def test_another_principal_never_reads_the_first_principals_schema() -> None:
    cache = MetadataCache()
    served: list[str] = []

    async def load_owner() -> str:
        served.append("owner")
        return "owner schema"

    async def load_other() -> str:
        served.append("other")
        return "other schema"

    owner_key = MetadataKey.for_principal(_partition(), source="ont", kind="schema")
    other_key = MetadataKey.for_principal(_partition(owner=OTHER_OWNER), source="ont", kind="schema")

    assert await cache.get_or_load(owner_key, load_owner) == "owner schema"
    # The second principal must pay for its own authorised read, not inherit the first one's.
    assert await cache.get_or_load(other_key, load_other) == "other schema"
    assert served == ["owner", "other"]


@pytest.mark.asyncio
async def test_parallel_cold_requests_perform_one_discovery() -> None:
    cache = MetadataCache()
    started = 0
    release = asyncio.Event()

    async def load() -> str:
        nonlocal started
        started += 1
        await release.wait()
        return "schema"

    key = MetadataKey.for_principal(_partition(), source="ont", kind="schema")
    waiting = [asyncio.create_task(cache.get_or_load(key, load)) for _ in range(4)]
    await asyncio.sleep(0)
    release.set()

    assert await asyncio.gather(*waiting) == ["schema"] * 4
    assert started == 1


@pytest.mark.asyncio
async def test_an_entry_is_revalidated_once_its_interval_has_passed() -> None:
    clock = Clock()
    cache = MetadataCache(ttl_seconds=300.0, clock=clock)
    loads = 0

    async def load() -> str:
        nonlocal loads
        loads += 1
        return f"schema-{loads}"

    key = MetadataKey.for_principal(_partition(), source="ont", kind="schema")

    assert await cache.get_or_load(key, load) == "schema-1"
    clock.now = 299.0
    assert await cache.get_or_load(key, load) == "schema-1"
    clock.now = 301.0
    assert await cache.get_or_load(key, load) == "schema-2"
    assert loads == 2


@pytest.mark.asyncio
async def test_an_invalidated_entry_is_read_again() -> None:
    cache = MetadataCache()
    loads = 0

    async def load() -> str:
        nonlocal loads
        loads += 1
        return f"schema-{loads}"

    key = MetadataKey.for_principal(_partition(), source="ont", kind="schema")
    await cache.get_or_load(key, load)
    cache.invalidate(key)

    assert await cache.get_or_load(key, load) == "schema-2"


@pytest.mark.asyncio
async def test_a_configuration_change_clears_every_principals_entry_for_that_source() -> None:
    cache = MetadataCache()

    async def load() -> str:
        return "schema"

    owner_key = MetadataKey.for_principal(_partition(), source="ont", kind="schema")
    other_key = MetadataKey.for_principal(_partition(owner=OTHER_OWNER), source="ont", kind="schema")
    untouched = MetadataKey.for_principal(_partition(), source="other-ont", kind="schema")
    for key in (owner_key, other_key, untouched):
        await cache.get_or_load(key, load)

    cache.invalidate_source("ont")

    assert not cache.has_fresh(owner_key)
    assert not cache.has_fresh(other_key)
    assert cache.has_fresh(untouched)


@pytest.mark.asyncio
async def test_a_failed_load_is_not_remembered_as_an_answer() -> None:
    cache = MetadataCache()
    attempts = 0

    async def load() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise PermissionError("the ontology refused the delegated read")
        return "schema"

    key = MetadataKey.for_principal(_partition(), source="ont", kind="schema")
    with pytest.raises(PermissionError):
        await cache.get_or_load(key, load)

    assert not cache.has_fresh(key)
    assert await cache.get_or_load(key, load) == "schema"


@pytest.mark.asyncio
async def test_the_cache_stays_bounded_as_principals_accumulate() -> None:
    clock = Clock()
    cache = MetadataCache(max_entries=3, clock=clock)

    async def load() -> str:
        return "schema"

    for index in range(6):
        clock.now = float(index)
        owner = UUID(f"{index:08d}-0000-0000-0000-000000000000")
        await cache.get_or_load(MetadataKey.for_principal(_partition(owner=owner), source="o", kind="schema"), load)

    # The oldest observations are dropped; the newest principal is still served warm.
    newest = MetadataKey.for_principal(
        _partition(owner=UUID("00000005-0000-0000-0000-000000000000")), source="o", kind="schema"
    )
    oldest = MetadataKey.for_principal(
        _partition(owner=UUID("00000000-0000-0000-0000-000000000000")), source="o", kind="schema"
    )
    assert cache.has_fresh(newest)
    assert not cache.has_fresh(oldest)
