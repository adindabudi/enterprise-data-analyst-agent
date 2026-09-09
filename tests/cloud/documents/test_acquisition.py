from __future__ import annotations

import pytest

from .evidence import DocumentAcceptanceObservations

pytestmark = pytest.mark.cloud


def test_deployed_acquisition_matches_lock_and_wrong_terms_build_fails(
    document_observations: DocumentAcceptanceObservations,
) -> None:
    assert document_observations.terms_failure_build_code == "terms_mismatch"
    assert len(document_observations.commit) == 40
    assert set(document_observations.bundles) == {"docx", "pdf", "pptx", "xlsx"}
    assert document_observations.tests["acquisition"] == "passed"
