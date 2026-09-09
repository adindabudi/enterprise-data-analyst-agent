from __future__ import annotations

import pytest
from eda_api.chat.service import _describe, _failure_reason, _status_detail

CAPACITY_ERROR = (
    "the graph query endpoint returned 404: CapacityNotActive - Internal error "
    "CapacityNotActive.Capacity ca766f47-3e58-43df-9e2a-28f3cd87a6f8 is not active"
)


def test_a_task_group_reports_what_actually_went_wrong() -> None:
    group = ExceptionGroup("unhandled errors in a TaskGroup", [ValueError(CAPACITY_ERROR)])

    # The bare string names the wrapper and nothing else, which sent us to App Insights to
    # find out that a Fabric capacity was paused.
    assert str(group).startswith("unhandled errors in a TaskGroup")
    assert _failure_reason(group) == CAPACITY_ERROR


def test_nested_groups_are_unwrapped_to_the_root() -> None:
    nested = ExceptionGroup("outer", [ExceptionGroup("inner", [RuntimeError("capacity is not active")])])

    assert _failure_reason(nested) == "capacity is not active"


def test_several_causes_are_all_named() -> None:
    group = ExceptionGroup("outer", [ValueError("first"), RuntimeError("second")])

    assert _failure_reason(group) == "first; second"


def test_an_ordinary_error_is_left_alone() -> None:
    assert _failure_reason(ValueError("plain message")) == "plain message"


def test_a_paused_capacity_is_explained_rather_than_quoted() -> None:
    detail = _status_detail(ExceptionGroup("unhandled errors in a TaskGroup", [ValueError(CAPACITY_ERROR)]))

    assert detail == "The source's Fabric capacity is paused, so no query can run until it is resumed."
    assert "CapacityNotActive" not in detail
    assert "404" not in detail


def test_no_reason_carries_an_identifier_out_to_the_reader() -> None:
    detail = _status_detail(ValueError("workspace b82afbde-8304-44c0-ac94-3cf69f6da909 refused the read"))

    # The specification forbids a target UUID reaching prompts, logs, telemetry or the UI.
    assert "b82afbde-8304-44c0-ac94-3cf69f6da909" not in detail
    assert detail == "workspace [id] refused the read"


def test_an_unrecognised_reason_is_still_passed_through() -> None:
    assert _status_detail(ValueError("the endpoint timed out")) == "the endpoint timed out"


@pytest.mark.asyncio
async def test_the_reason_reaches_the_caller_that_has_to_say_it() -> None:
    async def failing() -> str:
        raise ExceptionGroup("unhandled errors in a TaskGroup", [ValueError(CAPACITY_ERROR)])

    description, reason = await _describe("schema", failing())

    assert description == ""
    assert reason.startswith("The source's Fabric capacity is paused")
