"""Live answer text for in-process analysis tasks.

The model's visible text is published as `message.delta` events while a pass
runs, batched so a token stream does not become one Redis write per token. Each
stretch of text is a segment with its own attempt identifier; when a pass is
replaced (steering, output repair) or the model moves on to call a tool, the
segment is superseded, so the live view only ever shows text that can still
become the answer. The durable answer is the final message, never these events,
so a failure here only degrades the live view.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Protocol

from eda_runtime_state.events import EventDraft

logger = logging.getLogger(__name__)

MAX_DELTA_CHARS = 16_384
DEFAULT_BATCH_CHARS = 512
DEFAULT_BATCH_SECONDS = 0.25
PUBLISH_TIMEOUT_SECONDS = 2.0
MAX_PUBLISH_FAILURES = 3


class EventAppender(Protocol):
    async def append(self, draft: EventDraft) -> object: ...


class TaskStream:
    """The live text of one executing task."""

    def __init__(
        self,
        events: EventAppender,
        *,
        task_id: str,
        session_id: str,
        attempt_id: str,
        batch_chars: int,
        batch_seconds: float,
        clock: Callable[[], float],
    ) -> None:
        self._events = events
        self._task_id = task_id
        self._session_id = session_id
        self._attempt_id = attempt_id
        self._batch_chars = batch_chars
        self._batch_seconds = batch_seconds
        self._clock = clock
        self._segment = 0
        self._sequence = 0
        self._buffer: list[str] = []
        self._buffered = 0
        self._last_flush = clock()
        self._failures = 0
        self._lock = asyncio.Lock()

    @property
    def segment_id(self) -> str:
        return f"{self._attempt_id}.{self._segment}"

    @property
    def disabled(self) -> bool:
        return self._failures >= MAX_PUBLISH_FAILURES

    async def __call__(self, text: str) -> None:
        if not text or self.disabled:
            return
        self._buffer.append(text)
        self._buffered += len(text)
        if self._buffered >= self._batch_chars or self._clock() - self._last_flush >= self._batch_seconds:
            await self.flush()

    async def flush(self) -> None:
        async with self._lock:
            await self._flush_locked()

    async def boundary(self) -> None:
        """The model moved past this text (a tool call or a new pass); it can no longer be the answer."""
        async with self._lock:
            self._buffer.clear()
            self._buffered = 0
            if self._sequence == 0:
                return
            superseded = self.segment_id
            self._segment += 1
            self._sequence = 0
            await self._publish(
                EventDraft(
                    session_id=self._session_id,
                    task_id=self._task_id,
                    type="attempt.superseded",
                    payload={"supersededAttemptId": superseded, "replacementAttemptId": self.segment_id},
                ),
                critical=True,
            )

    async def _flush_locked(self) -> None:
        self._last_flush = self._clock()
        if not self._buffer:
            return
        text = "".join(self._buffer)
        self._buffer.clear()
        self._buffered = 0
        for start in range(0, len(text), MAX_DELTA_CHARS):
            self._sequence += 1
            await self._publish(
                EventDraft(
                    session_id=self._session_id,
                    task_id=self._task_id,
                    type="message.delta",
                    payload={"delta": text[start : start + MAX_DELTA_CHARS]},
                    response_attempt_id=self.segment_id,
                    attempt_sequence=self._sequence,
                )
            )

    async def _publish(self, draft: EventDraft, *, critical: bool = False) -> None:
        if self.disabled:
            return
        try:
            await asyncio.wait_for(self._events.append(draft), timeout=PUBLISH_TIMEOUT_SECONDS)
        except Exception:
            # Superseded text that cannot be withdrawn would mix with the next segment, so stop streaming instead.
            self._failures = MAX_PUBLISH_FAILURES if critical else self._failures + 1
            if self.disabled:
                logger.warning("live text disabled for task %s after event failures", self._task_id)
        else:
            self._failures = 0


class TaskStreams:
    """Live text for every task executing in this replica; `sink_for` is the runtime's delta sink factory."""

    def __init__(
        self,
        events: EventAppender | None,
        *,
        enabled: bool = True,
        batch_chars: int = DEFAULT_BATCH_CHARS,
        batch_seconds: float = DEFAULT_BATCH_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._events = events
        self._enabled = enabled and events is not None
        self._batch_chars = max(1, batch_chars)
        self._batch_seconds = max(0.0, batch_seconds)
        self._clock = clock
        self._streams: dict[str, TaskStream] = {}

    def open(self, task_id: str, *, session_id: str, attempt_id: str) -> None:
        if not self._enabled or self._events is None:
            return
        self._streams[task_id] = TaskStream(
            self._events,
            task_id=task_id,
            session_id=session_id,
            attempt_id=attempt_id,
            batch_chars=self._batch_chars,
            batch_seconds=self._batch_seconds,
            clock=self._clock,
        )

    def sink_for(self, task_id: str) -> TaskStream | None:
        return self._streams.get(task_id)

    async def begin_pass(self, task_id: str, pass_index: int) -> None:
        stream = self._streams.get(task_id)
        if stream is not None and pass_index > 0:
            await stream.boundary()

    async def close(self, task_id: str) -> None:
        # Nothing is flushed here: the pass flushed before publication, and text left over from a
        # failed or cancelled run must not appear after its terminal event.
        self._streams.pop(task_id, None)
