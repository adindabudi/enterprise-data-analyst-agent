from __future__ import annotations

import asyncio
from uuid import UUID

import pytest
from eda_api.fabric_auth.capacity import (
    CapacityMonitor,
    CapacityState,
    CapacityStatus,
    capacity_paused,
)
from eda_fabric_auth.msal_cache import FabricAuthorizationRequired

TENANT = UUID("11111111-1111-1111-1111-111111111111")
OWNER = UUID("22222222-2222-2222-2222-222222222222")
PAUSED_MESSAGE = "Internal error CapacityNotActive.Capacity ca766f47-3e58-43df-9e2a-28f3cd87a6f8 is not active"


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


class JsonRpcError:
    def __init__(self, data: object) -> None:
        self.message = "Internal error"
        self.data = data


class McpLikeError(Exception):
    """Shaped like the MCP client's error: Fabric's own code travels in the JSON-RPC error data."""

    def __init__(self, message: str, data: object) -> None:
        super().__init__(message)
        self.error = JsonRpcError(data)


def paused_handshake() -> BaseException:
    # What the real MCP client raised against a paused capacity: two task groups around the refusal.
    inner = ExceptionGroup("unhandled errors in a TaskGroup", [McpLikeError(PAUSED_MESSAGE, None)])
    return ExceptionGroup("unhandled errors in a TaskGroup", [inner])


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("the graph query endpoint returned 404: CapacityNotActive - " + PAUSED_MESSAGE),
        paused_handshake(),
        McpLikeError("Internal error", {"errorCode": "CapacityNotActive"}),
    ],
    ids=["graph-404", "mcp-handshake", "mcp-error-data"],
)
def test_a_paused_capacity_is_recognised_however_the_client_wraps_it(error: BaseException) -> None:
    assert capacity_paused(error)


def test_a_paused_capacity_is_recognised_behind_the_error_that_reported_it() -> None:
    try:
        try:
            raise RuntimeError(PAUSED_MESSAGE)
        except RuntimeError as refusal:
            raise ValueError("the Fabric query failed") from refusal
    except ValueError as reported:
        assert capacity_paused(reported)


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("the graph query endpoint returned 403: InsufficientPrivileges"),
        McpLikeError("Internal error", {"errorCode": "ItemNotFound"}),
        ExceptionGroup("unhandled errors in a TaskGroup", [TimeoutError("read timed out")]),
    ],
)
def test_other_failures_say_nothing_about_the_capacity(error: BaseException) -> None:
    assert not capacity_paused(error)


def test_an_observation_stays_fresh_for_its_own_window() -> None:
    clock = Clock()
    monitor = CapacityMonitor(active_fresh_seconds=60, paused_fresh_seconds=20, clock=clock)
    assert monitor.fresh() is None

    monitor.record(CapacityState.ACTIVE)
    clock.now += 59
    assert monitor.fresh() is CapacityState.ACTIVE
    clock.now += 1
    assert monitor.fresh() is None

    monitor.record(CapacityState.PAUSED)
    clock.now += 19
    assert monitor.fresh() is CapacityState.PAUSED
    clock.now += 1
    assert monitor.fresh() is None


def test_an_unknown_outcome_never_replaces_what_was_seen() -> None:
    monitor = CapacityMonitor(clock=Clock())
    monitor.record(CapacityState.PAUSED)

    monitor.record(CapacityState.UNKNOWN)
    monitor.observe_failure(RuntimeError("the graph query endpoint returned 500"))

    assert monitor.fresh() is CapacityState.PAUSED


def test_a_failed_read_that_fabric_refused_records_the_pause() -> None:
    monitor = CapacityMonitor(clock=Clock())

    monitor.observe_failure(paused_handshake())

    assert monitor.fresh() is CapacityState.PAUSED


@pytest.mark.asyncio
async def test_a_fresh_observation_answers_without_probing() -> None:
    monitor = CapacityMonitor(clock=Clock())
    monitor.record(CapacityState.PAUSED)
    probes = 0

    async def probe() -> None:
        nonlocal probes
        probes += 1

    assert await monitor.check(probe) is CapacityState.PAUSED
    assert probes == 0


@pytest.mark.asyncio
async def test_a_probe_the_endpoint_answers_is_a_running_capacity() -> None:
    clock = Clock()
    monitor = CapacityMonitor(clock=clock)

    async def probe() -> None:
        return None

    assert await monitor.check(probe) is CapacityState.ACTIVE
    assert monitor.fresh() is CapacityState.ACTIVE


@pytest.mark.asyncio
async def test_a_probe_fabric_refuses_is_a_paused_capacity() -> None:
    monitor = CapacityMonitor(clock=Clock())

    async def probe() -> None:
        raise paused_handshake()

    assert await monitor.check(probe) is CapacityState.PAUSED
    assert monitor.fresh() is CapacityState.PAUSED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [RuntimeError("connection reset"), FabricAuthorizationRequired("Fabric authorization is required.")],
    ids=["network", "reauth"],
)
async def test_a_probe_that_fails_for_another_reason_claims_nothing(failure: Exception) -> None:
    monitor = CapacityMonitor(clock=Clock())

    async def probe() -> None:
        raise failure

    assert await monitor.check(probe) is CapacityState.UNKNOWN
    assert monitor.fresh() is None


@pytest.mark.asyncio
async def test_a_probe_that_hangs_is_abandoned_as_unknown() -> None:
    monitor = CapacityMonitor(probe_timeout_seconds=0.01, clock=Clock())

    async def probe() -> None:
        await asyncio.sleep(5)

    assert await monitor.check(probe) is CapacityState.UNKNOWN


@pytest.mark.asyncio
async def test_concurrent_callers_share_one_probe() -> None:
    monitor = CapacityMonitor(clock=Clock())
    started = 0
    release = asyncio.Event()

    async def probe() -> None:
        nonlocal started
        started += 1
        await release.wait()

    waiting = [asyncio.create_task(monitor.check(probe)) for _ in range(5)]
    await asyncio.sleep(0)
    release.set()

    assert await asyncio.gather(*waiting) == [CapacityState.ACTIVE] * 5
    assert started == 1


@pytest.mark.asyncio
async def test_a_caller_that_leaves_does_not_cancel_the_shared_probe() -> None:
    monitor = CapacityMonitor(clock=Clock())
    release = asyncio.Event()

    async def probe() -> None:
        await release.wait()

    leaving = asyncio.create_task(monitor.check(probe))
    staying = asyncio.create_task(monitor.check(probe))
    await asyncio.sleep(0)
    leaving.cancel()
    release.set()

    assert await staying is CapacityState.ACTIVE
    with pytest.raises(asyncio.CancelledError):
        await leaving


class Tokens:
    def __init__(self, value: str = "owner-token") -> None:
        self.value = value
        self.owners: list[tuple[UUID, UUID]] = []

    async def acquire_for_owner(self, tenant_id: UUID, owner_object_id: UUID) -> str:
        self.owners.append((tenant_id, owner_object_id))
        return self.value


class Handshake:
    def __init__(self, error: BaseException | None = None) -> None:
        self.error = error
        self.tokens: list[str] = []

    async def probe_capacity(self, bearer_token: str) -> None:
        self.tokens.append(bearer_token)
        if self.error is not None:
            raise self.error


@pytest.mark.asyncio
async def test_the_status_probes_with_the_asking_owners_own_grant() -> None:
    tokens = Tokens()
    handshake = Handshake(paused_handshake())
    status = CapacityStatus(CapacityMonitor(clock=Clock()), tokens=tokens, source=handshake)

    assert await status.for_owner(TENANT, OWNER) is CapacityState.PAUSED
    assert tokens.owners == [(TENANT, OWNER)]
    assert handshake.tokens == ["owner-token"]


@pytest.mark.asyncio
async def test_the_status_does_not_probe_without_a_token() -> None:
    handshake = Handshake()
    status = CapacityStatus(CapacityMonitor(clock=Clock()), tokens=Tokens(value=""), source=handshake)

    assert await status.for_owner(TENANT, OWNER) is CapacityState.UNKNOWN
    assert handshake.tokens == []
