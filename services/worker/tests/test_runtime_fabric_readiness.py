from __future__ import annotations

import logging

import pytest
from eda_worker.fabric.readiness import FabricReadiness, FabricReadinessStatus
from eda_worker.runtime import isolate_optional_fabric_failure


@pytest.mark.parametrize(
    "status",
    [
        FabricReadinessStatus.DISABLED,
        FabricReadinessStatus.CONFIGURED,
        FabricReadinessStatus.READY,
    ],
)
def test_available_fabric_states_are_preserved(status: FabricReadinessStatus) -> None:
    readiness = FabricReadiness(status=status)

    assert isolate_optional_fabric_failure(readiness) is readiness


def test_failed_fabric_pack_is_withheld_without_crashing_the_runtime(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.ERROR)

    readiness = isolate_optional_fabric_failure(FabricReadiness(status=FabricReadinessStatus.FAILED))

    assert readiness.status is FabricReadinessStatus.DISABLED
    assert "continuing without Fabric tools" in caplog.text
