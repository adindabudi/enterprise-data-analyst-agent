from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from eda_contracts import ActivityEvent
from pydantic import TypeAdapter
from redis.asyncio import Redis

ACTIVITY_EVENT: TypeAdapter[Any] = cast(TypeAdapter[Any], TypeAdapter(ActivityEvent))
type RedisReadResponse = list[tuple[str, list[tuple[str, dict[str, str]]]]]

APPEND_SCRIPT = """
local sequence = redis.call('INCR', KEYS[2])
local event = cjson.decode(ARGV[1])
event['sequence'] = sequence
local stream_id = redis.call('XADD', KEYS[1], 'MAXLEN', '~', ARGV[2], '*', 'body', cjson.encode(event))
redis.call('EXPIRE', KEYS[1], ARGV[3])
redis.call('EXPIRE', KEYS[2], ARGV[3])
return {stream_id, tostring(sequence)}
"""

RESUMED_SCRIPT = """
local current = tonumber(redis.call('GET', KEYS[2]) or '0')
local dropped = tonumber(ARGV[4])
local event = cjson.decode(ARGV[1])
event['payload']['omittedFromSequence'] = current + 1
event['payload']['omittedToSequence'] = current + dropped
event['payload']['lastDurableSequence'] = current
local sequence = current + dropped + 1
event['sequence'] = sequence
redis.call('SET', KEYS[2], tostring(sequence), 'EX', ARGV[3])
local stream_id = redis.call('XADD', KEYS[1], 'MAXLEN', '~', ARGV[2], '*', 'body', cjson.encode(event))
redis.call('EXPIRE', KEYS[1], ARGV[3])
return {stream_id, tostring(sequence)}
"""


class CorruptStreamEntry(ValueError):
    pass


@dataclass(frozen=True)
class EventDraft:
    session_id: str
    task_id: str
    type: str
    payload: dict[str, Any] | list[Any]
    response_attempt_id: str | None = None
    attempt_sequence: int | None = None
    provenance_refs: tuple[str, ...] = ()

    def body(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "eventId": f"evt_{secrets.token_urlsafe(18)}",
            "sequence": 0,
            "sessionId": self.session_id,
            "taskId": self.task_id,
            "occurredAt": datetime.now(UTC).isoformat(),
            "type": self.type,
            "payload": self.payload,
        }
        if self.provenance_refs:
            value["provenanceRefs"] = list(self.provenance_refs)
        if self.response_attempt_id is not None:
            value["responseAttemptId"] = self.response_attempt_id
        if self.attempt_sequence is not None:
            value["attemptSequence"] = self.attempt_sequence
        return value


@dataclass(frozen=True)
class StreamEntry:
    stream_id: str
    event: ActivityEvent


class RedisEventStore:
    def __init__(self, redis: Redis, *, ttl_seconds: int, max_entries: int) -> None:
        self._redis = redis
        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries

    async def append(self, draft: EventDraft) -> StreamEntry:
        body = _validated_body(draft)
        result = await self._redis.eval(
            APPEND_SCRIPT,
            2,
            self._stream_key(draft.task_id),
            self._sequence_key(draft.task_id),
            json.dumps(body, separators=(",", ":")),
            str(self._max_entries),
            str(self._ttl_seconds),
        )
        stream_id, sequence = _script_result(result)
        body["sequence"] = sequence
        return StreamEntry(stream_id=stream_id, event=cast(ActivityEvent, ACTIVITY_EVENT.validate_python(body)))

    async def exists(self, task_id: str) -> bool:
        return bool(await self._redis.exists(self._stream_key(task_id)))

    async def append_resumed(self, draft: EventDraft, *, dropped_count: int) -> StreamEntry:
        if draft.type != "stream.resumed" or dropped_count < 1:
            raise ValueError("stream resume requires a positive dropped count")
        body = _validated_body(draft)
        result = await self._redis.eval(
            RESUMED_SCRIPT,
            2,
            self._stream_key(draft.task_id),
            self._sequence_key(draft.task_id),
            json.dumps(body, separators=(",", ":")),
            str(self._max_entries),
            str(self._ttl_seconds),
            str(dropped_count),
        )
        stream_id, sequence = _script_result(result)
        payload = cast(dict[str, Any], body["payload"])
        current_before = sequence - dropped_count - 1
        payload.update(
            {
                "lastDurableSequence": current_before,
                "omittedFromSequence": current_before + 1,
                "omittedToSequence": current_before + dropped_count,
            }
        )
        body["sequence"] = sequence
        return StreamEntry(stream_id=stream_id, event=cast(ActivityEvent, ACTIVITY_EVENT.validate_python(body)))

    async def read_after(self, task_id: str, cursor: str | None, *, block_ms: int = 15000) -> list[StreamEntry]:
        raw_response = await self._redis.xread({self._stream_key(task_id): cursor or "0-0"}, count=100, block=block_ms)
        response = cast(RedisReadResponse, raw_response)
        entries: list[StreamEntry] = []
        for _, raw_entries in response:
            for stream_id, fields in raw_entries:
                raw_body = fields.get("body")
                if not isinstance(raw_body, str):
                    raise CorruptStreamEntry("missing stream event body")
                try:
                    event = cast(ActivityEvent, ACTIVITY_EVENT.validate_python(json.loads(raw_body)))
                except (ValueError, TypeError) as error:
                    raise CorruptStreamEntry("invalid stream event") from error
                entries.append(StreamEntry(stream_id=str(stream_id), event=event))
        return entries

    @staticmethod
    def _stream_key(task_id: str) -> str:
        return f"task-stream:{{{task_id}}}"

    @staticmethod
    def _sequence_key(task_id: str) -> str:
        return f"task-sequence:{{{task_id}}}"


class DegradedEventBuffer:
    def __init__(self, *, max_bytes: int) -> None:
        self._max_bytes = max_bytes
        self.dropped_count = 0
        self.latest_milestone: EventDraft | None = None
        self.stdout_bytes = 0
        self.stderr_bytes = 0
        self.delta_characters = 0

    @property
    def size_bytes(self) -> int:
        value: dict[str, Any] = {
            "dropped": self.dropped_count,
            "stdout": self.stdout_bytes,
            "stderr": self.stderr_bytes,
            "delta": self.delta_characters,
            "milestone": self.latest_milestone.body() if self.latest_milestone is not None else None,
        }
        return min(len(json.dumps(value, separators=(",", ":")).encode()), self._max_bytes)

    def record(self, draft: EventDraft) -> None:
        self.dropped_count += 1
        if draft.type == "message.delta":
            payload = cast(dict[str, Any], draft.payload)
            delta = payload.get("delta")
            if isinstance(delta, str):
                self.delta_characters += len(delta)
            return
        if draft.type in {"code.stdout", "code.stderr"}:
            payload = cast(dict[str, Any], draft.payload)
            summary = payload.get("summary")
            if isinstance(summary, str):
                if draft.type == "code.stdout":
                    self.stdout_bytes += len(summary.encode())
                else:
                    self.stderr_bytes += len(summary.encode())
            return
        if draft.type in {"analysis_progress", "task.checkpointed"}:
            self.latest_milestone = _bounded_milestone(draft, self._max_bytes)


def _validated_body(draft: EventDraft) -> dict[str, Any]:
    body = draft.body()
    body["sequence"] = 1
    ACTIVITY_EVENT.validate_python(body)
    body["sequence"] = 0
    return body


def _script_result(result: Any) -> tuple[str, int]:
    if not isinstance(result, list):
        raise CorruptStreamEntry("invalid Redis script result")
    values = cast(list[Any], result)
    if len(values) != 2:
        raise CorruptStreamEntry("invalid Redis script result")
    stream_id, sequence = values
    if not isinstance(stream_id, str) or not isinstance(sequence, str):
        raise CorruptStreamEntry("invalid Redis script result")
    return stream_id, int(sequence)


def _bounded_milestone(draft: EventDraft, max_bytes: int) -> EventDraft:
    payload = cast(dict[str, Any], draft.payload)
    detail = payload.get("detail")
    if not isinstance(detail, str):
        return draft
    max_detail = max(0, min(len(detail), max_bytes // 4))
    return EventDraft(
        session_id=draft.session_id,
        task_id=draft.task_id,
        type=draft.type,
        payload={**payload, "detail": detail[:max_detail]},
        response_attempt_id=draft.response_attempt_id,
        attempt_sequence=draft.attempt_sequence,
        provenance_refs=draft.provenance_refs,
    )
