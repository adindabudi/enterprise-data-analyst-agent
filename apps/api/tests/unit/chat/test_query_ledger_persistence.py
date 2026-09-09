from __future__ import annotations

from typing import Any

from eda_api.chat.service import (
    QueryRun,
    _decode_runs,
    _encode_runs,
    _QueryLedger,
)


class StubStore:
    """Stands in for Redis so a second turn sees what the first one fetched."""

    def __init__(self) -> None:
        self.state: dict[str, Any] | None = None

    async def load(self, partition: object) -> dict[str, Any] | None:
        return self.state

    async def save(self, partition: object, state: dict[str, Any]) -> None:
        self.state = state


def test_rows_survive_a_round_trip_through_the_store() -> None:
    runs = (
        QueryRun(query="MATCH (p:patients) RETURN count(*)", rows="1200", source_alias="lamna"),
        QueryRun(query="MATCH (r:rooms) RETURN count(*)", rows="48"),
    )

    restored = _decode_runs(_encode_runs(runs))

    assert restored == runs


def test_a_later_turn_starts_from_the_rows_an_earlier_turn_fetched() -> None:
    first_turn = _QueryLedger()
    first_turn.record(QueryRun(query="MATCH (p:patients) RETURN p", rows="row-data", source_alias="lamna"))

    # The handoff turn builds a fresh ledger; without the seed it would hand over nothing.
    second_turn = _QueryLedger(initial=_decode_runs(_encode_runs(first_turn.runs())))

    assert second_turn.runs() == first_turn.runs()


def test_the_seed_obeys_the_handoff_limit() -> None:
    seeded = _QueryLedger(limit=2, initial=tuple(QueryRun(query=f"q{i}", rows=str(i)) for i in range(5)))

    assert [run.rows for run in seeded.runs()] == ["3", "4"]


def test_malformed_stored_rows_are_dropped_rather_than_crashing_the_turn() -> None:
    assert _decode_runs(None) == ()
    assert _decode_runs([{"query": "q"}, {"rows": "r"}, "text", {"query": "q", "rows": "r"}]) == (
        QueryRun(query="q", rows="r"),
    )


def test_the_turn_that_asked_survives_the_round_trip() -> None:
    runs = (
        QueryRun(query="MATCH (p:patients) RETURN p", rows="rows", source_alias="lamna", message_id="msg_first"),
        QueryRun(query="MATCH (r:rooms) RETURN r", rows="rows", message_id="msg_second"),
    )

    restored = _decode_runs(_encode_runs(runs))

    # Provenance has to name the turn; the ledger outlives a turn so executed_at cannot.
    assert [run.message_id for run in restored] == ["msg_first", "msg_second"]


def test_rows_stored_before_the_turn_was_tracked_still_load() -> None:
    legacy = [{"query": "MATCH (p) RETURN p", "rows": "rows", "sourceAlias": "lamna"}]

    restored = _decode_runs(legacy)

    assert restored[0].message_id is None
    assert restored[0].query == "MATCH (p) RETURN p"
