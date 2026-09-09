from __future__ import annotations

import pytest

from .evidence import FabricCloudObservations

pytestmark = pytest.mark.cloud


def test_silent_refresh_and_local_unlink_relink_pass(fabric_observations: FabricCloudObservations) -> None:
    assert fabric_observations.silent_refresh == "passed"
    assert fabric_observations.unlink_relink == "passed"
    assert fabric_observations.controls["silent_refresh"] == "passed"
    assert fabric_observations.controls["unlink_relink"] == "passed"
