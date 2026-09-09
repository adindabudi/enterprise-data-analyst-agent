from __future__ import annotations

import pytest

from .evidence import FabricCloudObservations

pytestmark = pytest.mark.cloud


def test_two_rls_users_receive_distinct_expected_result_hashes(
    fabric_observations: FabricCloudObservations,
) -> None:
    assert len(set(fabric_observations.rls_result_hashes)) == 2
    assert fabric_observations.controls["rls"] == "passed"
