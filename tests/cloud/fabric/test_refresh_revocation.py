from __future__ import annotations

import pytest

from .evidence import FabricCloudObservations

pytestmark = pytest.mark.cloud


def test_revoked_sign_in_sessions_force_reauthorization_without_unlink(
    fabric_observations: FabricCloudObservations,
) -> None:
    assert fabric_observations.revoked_refresh == "reauth_required"
    assert fabric_observations.controls["revoked_refresh"] == "passed"
