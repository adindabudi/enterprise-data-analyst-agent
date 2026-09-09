from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest
from eda_fabric_auth import FabricAuthorizationRequired
from eda_worker.fabric.errors import FabricProviderError, run_with_transport_retries


class Attempts:
    def __init__(self, failures: list[Exception]) -> None:
        self.failures = failures
        self.count = 0

    async def __call__(self) -> str:
        self.count += 1
        if self.failures:
            raise self.failures.pop(0)
        return "ok"


def recording_sleep(delays: list[float]) -> Callable[[float], Awaitable[None]]:
    async def sleep(delay: float) -> None:
        delays.append(delay)

    return sleep


@pytest.mark.asyncio
async def test_429_and_5xx_retry_at_most_three_transport_attempts() -> None:
    delays: list[float] = []
    operation = Attempts(
        [
            FabricProviderError(status_code=429, code="rate_limited", retry_after=0.25),
            FabricProviderError(status_code=503, code="provider_unavailable"),
        ]
    )

    result = await run_with_transport_retries(operation, sleep=recording_sleep(delays))

    assert result == "ok"
    assert operation.count == 3
    assert delays == [0.25, 1.0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        FabricAuthorizationRequired("Fabric authorization is required."),
        FabricProviderError(status_code=403, code="forbidden"),
        TimeoutError("deadline exceeded"),
        ValueError("schema drift"),
    ],
)
async def test_auth_timeout_and_schema_drift_never_retry(failure: Exception) -> None:
    operation = Attempts([failure])

    with pytest.raises(type(failure)):
        await run_with_transport_retries(operation, sleep=recording_sleep([]))
    assert operation.count == 1
