"""Exact-result reuse and duplicate suppression for one task's source reads.

Two identical questions inside one task are one question. Asking the source
twice costs the user the same seconds twice and can return two different
answers over mutable data, which then have to be reconciled. So an identical
query is executed once: a completed result is returned from the task's own
evidence, and a caller that arrives while the first call is still running waits
for that call instead of starting a second one.

The scope is deliberately one task. A new task fetches business values again,
because a later question must see current data rather than an earlier task's
numbers. Structure is cached separately and for longer; see `metadata_cache`.

A failure is never stored. An error is not evidence that the source holds
nothing, and a timeout is not an empty result.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field

from eda_runtime_state.models import TaskPartition


@dataclass(frozen=True, slots=True)
class QueryOutcome:
    rows: str
    reused: bool


def query_fingerprint(
    partition: TaskPartition,
    *,
    source: str,
    route: str,
    query: str,
    options: Mapping[str, object] | None = None,
) -> str:
    """A stable identity for one exact structured read.

    Nothing is normalised beyond the surrounding whitespace a caller cannot
    control: changing a literal, an identifier or a parameter changes the
    question, so it must change the fingerprint.
    """

    canonical = json.dumps(
        {
            "tenant": str(partition.tenant_id),
            "owner": str(partition.owner_object_id),
            "session": partition.session_id,
            "source": source,
            "route": route,
            "query": query.strip(),
            "options": dict(sorted((options or {}).items())),
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class _InFlight:
    done: asyncio.Event = field(default_factory=asyncio.Event)
    rows: str | None = None
    error: BaseException | None = None


class TaskQueryCoalescer:
    """Task-scoped exact-result reuse and in-flight coalescing."""

    def __init__(self) -> None:
        self._completed: dict[str, str] = {}
        self._inflight: dict[str, _InFlight] = {}
        self._executed = 0
        self._reused = 0

    @property
    def executed(self) -> int:
        """Reads that reached the provider."""
        return self._executed

    @property
    def reused(self) -> int:
        """Reads answered from this task's own evidence or from a call already running."""
        return self._reused

    async def run(self, fingerprint: str, execute: Callable[[], Awaitable[str]]) -> QueryOutcome:
        completed = self._completed.get(fingerprint)
        if completed is not None:
            self._reused += 1
            return QueryOutcome(rows=completed, reused=True)

        pending = self._inflight.get(fingerprint)
        if pending is not None:
            await pending.done.wait()
            if pending.error is not None:
                raise pending.error
            if pending.rows is None:
                raise RuntimeError("the shared query finished without a result")
            self._reused += 1
            return QueryOutcome(rows=pending.rows, reused=True)

        current = _InFlight()
        self._inflight[fingerprint] = current
        try:
            rows = await execute()
        except BaseException as error:
            current.error = error
            raise
        else:
            current.rows = rows
            self._completed[fingerprint] = rows
            self._executed += 1
            return QueryOutcome(rows=rows, reused=False)
        finally:
            self._inflight.pop(fingerprint, None)
            current.done.set()
