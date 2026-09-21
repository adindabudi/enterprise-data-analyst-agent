from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

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
        for page in reader.pages:
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
        raise ValueError("PDF rendering requires pdftoppm (Poppler).")
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
            timeout=60,
        )
        rendered_pages = sorted(directory.glob("page-*.png"))
        if len(rendered_pages) != 1:
            raise ValueError("Poppler did not render exactly one PDF page.")
        return rendered_pages[0].read_bytes()
