from __future__ import annotations

import pytest

from .evidence import FabricCloudObservations

pytestmark = pytest.mark.cloud


def test_product_and_fabric_tenants_are_distinct_and_login_is_owner_bound(
    fabric_observations: FabricCloudObservations,
) -> None:
    assert len(set(fabric_observations.tenant_hashes)) == 2
    assert fabric_observations.controls["cross_tenant_login"] == "passed"
