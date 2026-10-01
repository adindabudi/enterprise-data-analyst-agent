"""Owner-scoped, revalidating cache for Fabric source metadata.

Entity and property names describe a customer's business, so a metadata entry
belongs to the principal that was authorised to read it. A process-wide cache
keyed only by the endpoint would hand one customer's schema to the next caller
and let a cached entry stand in for a permission check. Metadata that carries no
customer data, such as a provider's fixed tool contract, is keyed by endpoint
instead so every principal pays for it once.

Structure is cached here; returned business values are not. Those stay
task-scoped, because a later task must see current data rather than an earlier
task's numbers.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Final, cast

from eda_runtime_state.models import TaskPartition

# No source version or change notification is available on these endpoints, so
# an entry is revalidated on an interval instead. This is an application policy,
# not a freshness guarantee from Fabric.
DEFAULT_TTL_SECONDS: Final = 300.0
# One deployment serves many principals; the cache is bounded so a long-running
# instance cannot accumulate every principal's schema forever.
DEFAULT_MAX_ENTRIES: Final = 512


@dataclass(frozen=True, slots=True)
class MetadataKey:
    """Identity of one cached metadata entry.

    `tenant_id` and `owner_object_id` are empty only for entries that hold no
    customer data; every entry describing a customer's data carries them.
    """

    tenant_id: str
    owner_object_id: str
    source: str
    kind: str

    @classmethod
    def for_principal(cls, partition: TaskPartition, *, source: str, kind: str) -> MetadataKey:
        return cls(
            tenant_id=str(partition.tenant_id),
            owner_object_id=str(partition.owner_object_id),
            source=source,
            kind=kind,
        )

    @classmethod
    def for_endpoint(cls, *, source: str, kind: str) -> MetadataKey:
        """For metadata owned by the provider rather than the customer."""
        return cls(tenant_id="", owner_object_id="", source=source, kind=kind)


@dataclass(slots=True)
class _Entry:
    value: object
    observed_at: float


class MetadataCache:
    """A bounded cache with per-key single-flight loading.

    Parallel first requests for the same key wait for one load instead of each
    paying for a cold discovery.
    """

    def __init__(
        self,
        *,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl_seconds = ttl_seconds
        self._max_entries = max(1, max_entries)
        self._clock = clock
        self._entries: dict[MetadataKey, _Entry] = {}
        self._locks: dict[MetadataKey, asyncio.Lock] = {}

    async def get_or_load[T](self, key: MetadataKey, loader: Callable[[], Awaitable[T]]) -> T:
        fresh = self._fresh(key)
        if fresh is not None:
            return cast(T, fresh.value)
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        async with lock:
            # A parallel caller may have completed the same load while this one waited.
            fresh = self._fresh(key)
            if fresh is not None:
                return cast(T, fresh.value)
            value = await loader()
            self._store(key, value)
            return value

    def has_fresh(self, key: MetadataKey) -> bool:
        return self._fresh(key) is not None

    def invalidate(self, key: MetadataKey) -> None:
        """Drop one entry, for a schema or permission error or an explicit refresh."""
        self._entries.pop(key, None)

    def invalidate_source(self, source: str) -> None:
        """Drop every principal's entries for one source, for a configuration change."""
        for key in [entry for entry in self._entries if entry.source == source]:
            del self._entries[key]

    def _fresh(self, key: MetadataKey) -> _Entry | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        if self._clock() - entry.observed_at >= self._ttl_seconds:
            del self._entries[key]
            return None
        return entry

    def _store(self, key: MetadataKey, value: object) -> None:
        self._entries[key] = _Entry(value=value, observed_at=self._clock())
        self._evict()

    def _evict(self) -> None:
        if len(self._entries) <= self._max_entries:
            return
        ordered = sorted(self._entries.items(), key=lambda item: item[1].observed_at)
        for key, _entry in ordered[: len(self._entries) - self._max_entries]:
            del self._entries[key]
        for key in [key for key, lock in self._locks.items() if key not in self._entries and not lock.locked()]:
            del self._locks[key]
