from __future__ import annotations

import pytest

from .evidence import FabricCloudObservations

pytestmark = pytest.mark.cloud


def test_profile_bound_harness_passes_forced_and_automatic_routing(
    fabric_observations: FabricCloudObservations,
) -> None:
    assert fabric_observations.model_profile == "gpt-5.6-terra-medium-v1"
    assert fabric_observations.served_model == "gpt-5.6-terra"
    assert fabric_observations.served_snapshot == "2026-07-09"
    assert fabric_observations.forced_first_routing == "passed"
    assert fabric_observations.automatic_routing == "passed"
    assert fabric_observations.controls["harness_forced_first"] == "passed"
    assert fabric_observations.controls["harness_automatic"] == "passed"
