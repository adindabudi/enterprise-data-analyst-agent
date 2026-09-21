from __future__ import annotations

import hashlib
import io

import pytest
from eda_artifacts import pdf as pdf_module
from eda_artifacts.documents import generate_pdf
from eda_artifacts.pdf import validate_pdf
from pypdf import PdfReader


def valid_pdf() -> bytes:
    return generate_pdf("Document Pack fixture")


def renderer(_payload: bytes, page_number: int) -> bytes:
    return f"rendered-page-{page_number}".encode()


def test_pdf_profile_rejects_an_unreadable_xref() -> None:
    report = validate_pdf(b"%PDF-1.7\ntruncated", renderer=renderer)

    assert report.passed is False
    assert "xref" in report.failures[0].lower() or "startxref" in report.failures[0].lower()


def test_pdf_profile_records_page_count_and_render_hashes() -> None:
    report = validate_pdf(valid_pdf(), renderer=renderer)

    assert report.passed is True
    assert report.page_count == 1
    assert report.page_render_sha256 == (hashlib.sha256(b"rendered-page-0").hexdigest(),)


def test_pdf_renderer_unavailable_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pdf_module.shutil, "which", lambda _name: None)

    report = validate_pdf(valid_pdf())

    assert report.passed is False
    assert report.failures == ("PDF rendering requires pdftoppm (Poppler).",)


@pytest.mark.parametrize("title", ["Revenue (FY26) & costs", "Caf\u00e9 (FY26)"])
def test_pdf_writer_preserves_title_and_extractable_text(title: str) -> None:
    document = PdfReader(io.BytesIO(generate_pdf(title)))

    assert document.metadata is not None
    assert document.metadata.title == title
    assert title in document.pages[0].extract_text()
