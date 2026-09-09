from __future__ import annotations

import pytest

from .evidence import FabricCloudObservations

pytestmark = pytest.mark.cloud


def test_all_scanned_surfaces_are_secret_free_and_grants_are_ciphertext(
    fabric_observations: FabricCloudObservations,
) -> None:
    assert fabric_observations.forbidden_markers_found == 0
    assert fabric_observations.grant_ciphertext_verified is True
    assert fabric_observations.controls["no_secrets"] == "passed"
