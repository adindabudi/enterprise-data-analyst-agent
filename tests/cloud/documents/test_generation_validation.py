from __future__ import annotations

import pytest

from .evidence import DocumentAcceptanceObservations

pytestmark = pytest.mark.cloud


def test_deployed_harness_generates_validates_renders_and_publishes_each_format_twice(
    document_observations: DocumentAcceptanceObservations,
) -> None:
    kinds = {"docx", "pdf", "pptx", "xlsx"}
    assert set(document_observations.first_artifact_sha256) == kinds
    assert set(document_observations.second_artifact_sha256) == kinds
    assert all(
        document_observations.first_artifact_sha256[kind] != document_observations.second_artifact_sha256[kind]
        for kind in kinds
    )
    assert set(document_observations.validation_report_sha256) == kinds
    assert set(document_observations.preview_sha256) == kinds
    assert document_observations.published_versions == {"docx": 2, "pdf": 2, "pptx": 2, "xlsx": 2}
    assert document_observations.tests["generation_validation"] == "passed"
