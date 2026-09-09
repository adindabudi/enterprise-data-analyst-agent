import pytest
from eda_worker.tools.contracts import CapabilityResult, ValidationProfile
from pydantic import ValidationError


def test_result_rejects_unbounded_summary() -> None:
    with pytest.raises(ValidationError):
        CapabilityResult(status="ok", summary="x" * 4001)


def test_document_validation_profiles_are_explicit_and_bounded() -> None:
    assert {
        ValidationProfile.DOCUMENT_PPTX.value,
        ValidationProfile.DOCUMENT_DOCX.value,
        ValidationProfile.DOCUMENT_XLSX.value,
        ValidationProfile.DOCUMENT_PDF.value,
    } == {"document_pptx", "document_docx", "document_xlsx", "document_pdf"}
