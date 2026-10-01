"""Admission, ownership, and conversation exclusivity for in-process analysis tasks.

One document, the execution ledger (`capacity:analysis`), holds one entry per
executing task: the task, its conversation, the owning replica, a fence, and an
expiry. Keeping all of it in one document makes every claim a single atomic
compare-and-swap, so these hold together or not at all:

* no more than `limit` tasks execute across every replica and revision;
* no conversation has two executing tasks, which would interleave one history;
* one task has exactly one owner, identified by a fence that is a fresh random
  value on every claim, so a stale executor can never mistake a later claim for
  its own (a monotonic counter could restart if its document were recreated).

Nothing here lives on the task record: every task write is an ETag
compare-and-swap, and renewals every few seconds would make checkpoint and
command writes fail spuriously. Expiry is decided from the timestamps written
here, never from Cosmos TTL, which the runtime container does not enable.
"""

from __future__ import annotations

import asyncio
import copy
import secrets
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol, cast

from azure.core import MatchConditions
from azure.cosmos.exceptions import CosmosHttpResponseError, CosmosResourceNotFoundError
from pydantic import Field

from .models import RuntimeModel

EXECUTION_LEDGER_ID = "capacity:analysis"
# Enough attempts to ride out renewals from several tasks on two overlapping revisions.
MAX_CAS_ATTEMPTS = 12


class LeaseConflict(RuntimeError):
    """A compare-and-swap kept losing; the caller must not assume it holds anything."""


class ExecutionEntry(RuntimeModel):
    task_id: str = Field(pattern=r"^task_[A-Za-z0-9_-]{8,}$")
    session_id: str = Field(min_length=1, max_length=128)
    owner: str = Field(min_length=1, max_length=128)
    fence: str = Field(pattern=r"^[a-f0-9]{32}$")
    expires_at: datetime


class ExecutionLedger(RuntimeModel):
    id: Literal["capacity:analysis"] = EXECUTION_LEDGER_ID
    record_type: Literal["executionLedger"] = "executionLedger"
    entries: tuple[ExecutionEntry, ...] = ()
    etag: str = Field(alias="_etag", default="new")

    def live(self, now: datetime) -> tuple[ExecutionEntry, ...]:
        return tuple(entry for entry in self.entries if entry.expires_at > now)


class ExecutionClaim(RuntimeModel):
    task_id: str
    session_id: str
    owner: str
    fence: str


ClaimOutcome = Literal["claimed", "capacity_full", "conversation_busy", "owned_elsewhere"]


class LedgerContainer(Protocol):
    async def read_item(self, item: str, partition_key: str) -> dict[str, Any]: ...

    async def create_item(self, body: dict[str, Any]) -> dict[str, Any]: ...

    async def replace_item(
        self,
        item: str,
        body: dict[str, Any],
        *,
        etag: str,
        match_condition: MatchConditions,
    ) -> dict[str, Any]: ...


class ExecutionLedgerStore:
    def __init__(
        self,
        container: LedgerContainer,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._container = container
        self._clock = clock or (lambda: datetime.now(UTC))

    def now(self) -> datetime:
        return self._clock()

    async def claim(
        self,
        task_id: str,
        session_id: str,
        owner: str,
        *,
        ttl: timedelta,
        limit: int,
        exclusive_conversation: bool = True,
    ) -> tuple[ClaimOutcome, ExecutionClaim | None]:
        """Take permission to execute one task.

        An expired entry for the same task is adopted, which is how an interrupted
        task is recovered; a live entry held by another owner is never taken.
        `exclusive_conversation=False` is for settling a task that never ran
        (cancelling or expiring it): it writes no history, so it need not wait for
        the conversation, but it still needs to be the task's only owner.
        """
        for _ in range(MAX_CAS_ATTEMPTS):
            ledger = await self._ledger()
            now = self.now()
            live = list(ledger.live(now))
            current = next((entry for entry in live if entry.task_id == task_id), None)
            if current is not None and current.owner != owner:
                return "owned_elsewhere", None
            others = [entry for entry in live if entry.task_id != task_id]
            if exclusive_conversation and any(entry.session_id == session_id for entry in others):
                return "conversation_busy", None
            if len(others) >= limit:
                return "capacity_full", None
            entry = ExecutionEntry(
                task_id=task_id,
                session_id=session_id,
                owner=owner,
                fence=secrets.token_hex(16),
                expires_at=now + ttl,
            )
            if await self._write(ledger, (*others, entry)):
                return "claimed", ExecutionClaim(task_id=task_id, session_id=session_id, owner=owner, fence=entry.fence)
        raise LeaseConflict("execution ledger kept changing")

    async def renew(self, claims: Mapping[str, str], *, ttl: timedelta) -> set[str]:
        """Extend every entry whose fence still matches, in one write; returns the task IDs still held."""
        if not claims:
            return set()
        for _ in range(MAX_CAS_ATTEMPTS):
            ledger = await self._ledger()
            now = self.now()
            live = ledger.live(now)
            held = {entry.task_id for entry in live if claims.get(entry.task_id) == entry.fence}
            if not held:
                return set()
            renewed = tuple(
                entry.model_copy(update={"expires_at": now + ttl}) if entry.task_id in held else entry for entry in live
            )
            if await self._write(ledger, renewed):
                return held
        raise LeaseConflict("execution ledger kept changing")

    async def release(self, claim: ExecutionClaim) -> None:
        """Remove the entry only if it is still this claim's; a stale release frees nothing."""
        for _ in range(MAX_CAS_ATTEMPTS):
            ledger = await self._ledger()
            remaining = tuple(
                entry for entry in ledger.entries if not (entry.task_id == claim.task_id and entry.fence == claim.fence)
            )
            if len(remaining) == len(ledger.entries):
                return
            if await self._write(ledger, remaining):
                return
        raise LeaseConflict("execution ledger kept changing")

    async def is_held(self, claim: ExecutionClaim) -> bool:
        ledger = await self._ledger()
        now = self.now()
        return any(entry.task_id == claim.task_id and entry.fence == claim.fence for entry in ledger.live(now))

    async def snapshot(self) -> ExecutionLedger:
        return await self._ledger()

    async def _ledger(self) -> ExecutionLedger:
        try:
            return ExecutionLedger.model_validate(
                await self._container.read_item(item=EXECUTION_LEDGER_ID, partition_key=EXECUTION_LEDGER_ID)
            )
        except CosmosResourceNotFoundError:
            pass
        try:
            return ExecutionLedger.model_validate(
                await self._container.create_item(ExecutionLedger().model_dump(mode="json", exclude={"etag"}))
            )
        except CosmosHttpResponseError as error:
            if error.status_code != 409:
                raise
        return ExecutionLedger.model_validate(
            await self._container.read_item(item=EXECUTION_LEDGER_ID, partition_key=EXECUTION_LEDGER_ID)
        )

    async def _write(self, ledger: ExecutionLedger, entries: tuple[ExecutionEntry, ...]) -> bool:
        body = ledger.model_copy(update={"entries": entries}).model_dump(mode="json", exclude={"etag"})
        try:
            await self._container.replace_item(
                item=EXECUTION_LEDGER_ID,
                body=body,
                etag=ledger.etag,
                match_condition=MatchConditions.IfNotModified,
            )
        except CosmosHttpResponseError as error:
            if error.status_code in {409, 412}:
                return False
            raise
        return True


class InMemoryLedgerContainer:
    """A document container with Cosmos ETag semantics, for tests and local runs."""

    def __init__(self) -> None:
        self._documents: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def read_item(self, item: str, partition_key: str) -> dict[str, Any]:
        del partition_key
        async with self._lock:
            document = self._documents.get(item)
            if document is None:
                raise CosmosResourceNotFoundError(message="not found")
            return copy.deepcopy(document)

    async def create_item(self, body: dict[str, Any]) -> dict[str, Any]:
        async with self._lock:
            item_id = cast(str, body["id"])
            if item_id in self._documents:
                raise CosmosHttpResponseError(status_code=409, message="conflict")
            stored = {**copy.deepcopy(body), "_etag": secrets.token_hex(8)}
            self._documents[item_id] = stored
            return copy.deepcopy(stored)

    async def replace_item(
        self,
        item: str,
        body: dict[str, Any],
        *,
        etag: str,
        match_condition: MatchConditions,
    ) -> dict[str, Any]:
        async with self._lock:
            current = self._documents.get(item)
            if current is None:
                raise CosmosResourceNotFoundError(message="not found")
            if match_condition is MatchConditions.IfNotModified and current.get("_etag") != etag:
                raise CosmosHttpResponseError(status_code=412, message="precondition failed")
            stored = {**copy.deepcopy(body), "_etag": secrets.token_hex(8)}
            self._documents[item] = stored
            return copy.deepcopy(stored)
