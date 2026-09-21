from __future__ import annotations

import base64
import io
import subprocess
import zipfile
from pathlib import Path
from shutil import which

import pytest
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[3]
SANDBOX = ROOT / "services/sandbox"
NODE = which("node")


def test_vendored_pptx_runtime_writes_text_and_embedded_image(tmp_path: Path) -> None:
    if NODE is None or not (SANDBOX / "node_modules/pptxgenjs").is_dir():
        pytest.skip("install the sandbox npm dependencies to run the real PPTX generation test")
    image = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aT0cAAAAASUVORK5CYII="
    )
    output = tmp_path / "presentation.pptx"
    result = subprocess.run(  # noqa: S603
        [
            NODE,
            "--input-type=module",
            "-e",
            "import PptxGenJS from 'pptxgenjs';"
            "const presentation = new PptxGenJS();"
            "const slide = presentation.addSlide();"
            "slide.addText('Verified local distribution', {x:1,y:1,w:6,h:1});"
            "slide.addImage({data:'data:image/png;base64,'+process.argv[2],x:1,y:2,w:1,h:1});"
            "await presentation.writeFile({fileName:process.argv[1]});",
            str(output),
            base64.b64encode(image).decode(),
        ],
        cwd=SANDBOX,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    with zipfile.ZipFile(output) as archive:
        assert archive.testzip() is None
        assert b"Verified local distribution" in archive.read("ppt/slides/slide1.xml")
        images = [name for name in archive.namelist() if name.startswith("ppt/media/") and name.endswith(".png")]
        assert len(images) == 1
        assert archive.read(images[0]) == image

    parsed = subprocess.run(  # noqa: S603
        [
            NODE,
            "-e",
            "const Reader=require('pptx2json');"
            "new Reader().toJson(process.argv[1]).then(value=>console.log(JSON.stringify(value)));",
            str(output),
        ],
        cwd=SANDBOX,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert parsed.returncode == 0, parsed.stderr
    assert "Verified local distribution" in parsed.stdout


def test_pdf_lib_writes_a_readable_document(tmp_path: Path) -> None:
    if NODE is None or not (SANDBOX / "node_modules/pdf-lib").is_dir():
        pytest.skip("install the sandbox npm dependencies to run the real PDF generation test")
    output = tmp_path / "report.pdf"
    result = subprocess.run(  # noqa: S603
        [
            NODE,
            "--input-type=module",
            "-e",
            "import {PDFDocument,StandardFonts} from 'pdf-lib';"
            "import {writeFile} from 'node:fs/promises';"
            "const document=await PDFDocument.create();"
            "const font=await document.embedFont(StandardFonts.Helvetica);"
            "document.addPage().drawText('Verified PDF output',{font,x:72,y:700});"
            "await writeFile(process.argv[1],await document.save());",
            str(output),
        ],
        cwd=SANDBOX,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    document = PdfReader(io.BytesIO(output.read_bytes()))
    assert len(document.pages) == 1
    assert "Verified PDF output" in document.pages[0].extract_text()
