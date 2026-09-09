from __future__ import annotations

import pytest

from .evidence import DocumentAcceptanceObservations

pytestmark = pytest.mark.cloud


def test_proprietary_skill_bodies_are_absent_from_every_scanned_surface(
    document_observations: DocumentAcceptanceObservations,
) -> None:
    assert document_observations.leaked_skill_markers == 0
    assert set(document_observations.scanned_surfaces) == {
        "application_logs",
        "app_insights",
        "cosmos",
        "blob_manifests",
    }
    assert document_observations.tests["no_leak"] == "passed"
