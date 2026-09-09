from __future__ import annotations

import pytest

from .evidence import FabricCloudObservations

pytestmark = pytest.mark.cloud


def test_fabric_query_evidence_and_control_totals_reconcile(
    fabric_observations: FabricCloudObservations,
) -> None:
    assert fabric_observations.provenance_reconciled is True
    assert fabric_observations.controls["provenance"] == "passed"
