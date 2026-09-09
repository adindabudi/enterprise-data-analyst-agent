from __future__ import annotations

import pytest

from .evidence import FabricCloudObservations

pytestmark = pytest.mark.cloud


def test_provider_contract_runtime_allowlist_and_no_fallback_paths(
    fabric_observations: FabricCloudObservations,
) -> None:
    assert set(fabric_observations.provider_tool_names) == {
        "DiscoverArtifacts",
        "GetReportMetadata",
        "GetSemanticModelSchema",
        "ValueSearch",
        "ExecuteQuery",
        "ResolveReportIdFromUrl",
    }
    assert set(fabric_observations.runtime_tool_names) == {
        "GetSemanticModelSchema",
        "ValueSearch",
        "ExecuteQuery",
    }
    assert set(fabric_observations.invoked_tool_names) <= set(fabric_observations.runtime_tool_names)
    assert fabric_observations.fallback_paths_observed == ()
    assert fabric_observations.controls["provider_contract"] == "passed"
    assert fabric_observations.controls["runtime_allowlist"] == "passed"
    assert fabric_observations.controls["no_fallback"] == "passed"
