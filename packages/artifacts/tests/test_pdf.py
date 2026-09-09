from __future__ import annotations

import hashlib
import io

from eda_artifacts.pdf import validate_pdf
from reportlab.pdfgen.canvas import Canvas


def valid_pdf() -> bytes:
    output = io.BytesIO()
    canvas = Canvas(output)
    canvas.drawString(72, 720, "Document Pack fixture")
    canvas.showPage()
    canvas.save()
    return output.getvalue()


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
