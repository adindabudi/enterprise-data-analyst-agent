from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable


class FabricProviderError(RuntimeError):
    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code[:80]
        self.retry_after = retry_after


async def run_with_transport_retries[T](
    operation: Callable[[], Awaitable[T]],
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> T:
    fallback_delays = (0.5, 1.0)
    for attempt in range(3):
        try:
            return await operation()
        except FabricProviderError as error:
            retryable = error.status_code == 429 or error.status_code >= 500
            if not retryable or attempt == 2:
                raise
            delay = error.retry_after
            if delay is None or delay < 0 or delay > 30:
                delay = fallback_delays[attempt]
            await sleep(delay)
    raise RuntimeError("unreachable Fabric retry state")
