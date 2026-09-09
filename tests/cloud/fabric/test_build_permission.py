from __future__ import annotations

import pytest

from .evidence import FabricCloudObservations

pytestmark = pytest.mark.cloud


def test_no_build_user_receives_nonretryable_denial_without_result(
    fabric_observations: FabricCloudObservations,
) -> None:
    assert fabric_observations.build_denial_code == "build_permission_denied"
    assert fabric_observations.controls["build_permission"] == "passed"
