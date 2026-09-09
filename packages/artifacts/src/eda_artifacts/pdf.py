from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, cast

import pdfplumber
import pypdfium2
from pypdf import PdfReader

type PageRenderer = Callable[[bytes, int], bytes]


@dataclass(frozen=True)
class PdfValidationReport:
    passed: bool
    failures: tuple[str, ...]
    page_count: int = 0
    page_render_sha256: tuple[str, ...] = ()


def validate_pdf(payload: bytes, *, renderer: PageRenderer | None = None) -> PdfValidationReport:
    if not payload.startswith(b"%PDF-"):
        return PdfValidationReport(passed=False, failures=("PDF header is invalid.",))
    try:
        reader = PdfReader(BytesIO(payload), strict=False)
        page_count = len(reader.pages)
        if page_count < 1:
            return PdfValidationReport(passed=False, failures=("PDF contains no pages.",))
        with pdfplumber.open(BytesIO(payload)) as document:
            if len(document.pages) != page_count:
                return PdfValidationReport(passed=False, failures=("PDF page count is inconsistent.",))
            for page in document.pages:
                page.extract_text()
    except Exception:
        return PdfValidationReport(passed=False, failures=("PDF xref or startxref is unreadable.",))

    render = renderer or render_page_with_poppler
    try:
        render_hashes = tuple(
            hashlib.sha256(render(payload, page_number)).hexdigest() for page_number in range(page_count)
        )
    except ValueError as error:
        return PdfValidationReport(passed=False, failures=(str(error),), page_count=page_count)
    return PdfValidationReport(passed=True, failures=(), page_count=page_count, page_render_sha256=render_hashes)


def render_page_with_poppler(payload: bytes, page_number: int) -> bytes:
    executable = shutil.which("pdftoppm")
    if executable is None:
        return render_page_with_pdfium(payload, page_number)
    with tempfile.TemporaryDirectory() as temporary_directory:
        directory = Path(temporary_directory)
        source = directory / "document.pdf"
        output_prefix = directory / "page"
        source.write_bytes(payload)
        subprocess.run(  # noqa: S603 - fixed Poppler executable and generated temporary paths; no shell is used.
            [
                executable,
                "-f",
                str(page_number + 1),
                "-l",
                str(page_number + 1),
                "-png",
                str(source),
                str(output_prefix),
            ],
            check=True,
            capture_output=True,
        )
        rendered_pages = sorted(directory.glob("page-*.png"))
        if len(rendered_pages) != 1:
            raise ValueError("Poppler did not render exactly one PDF page.")
        return rendered_pages[0].read_bytes()


def render_page_with_pdfium(payload: bytes, page_number: int) -> bytes:
    document = pypdfium2.PdfDocument(payload)
    try:
        if not 0 <= page_number < len(document):
            raise ValueError("PDF page number is out of range.")
        page = document[page_number]
        try:
            # pypdfium2 ships no type stubs; type the render boundary explicitly.
            bitmap: Any = cast("Any", page).render(scale=1.5)
            image: Any = bitmap.to_pil()
            output = BytesIO()
            image.save(output, format="PNG")
            return output.getvalue()
        finally:
            page.close()
    finally:
        document.close()
