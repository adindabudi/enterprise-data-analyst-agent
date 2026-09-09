from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from openpyxl import Workbook
from reportlab.pdfgen import canvas

INPUTS = Path("inputs")
OUTPUTS = Path("outputs")
OUTPUTS.mkdir(exist_ok=True)
parameter_files = list(INPUTS.glob("*.json"))
if len(parameter_files) != 1:
    raise ValueError("document benchmark requires exactly one JSON parameter artifact")
parameters = json.loads(parameter_files[0].read_text(encoding="utf-8"))
document_type = parameters["documentType"]

if document_type == "pdf":
    output = (OUTPUTS / "document.pdf").resolve()
    pdf = canvas.Canvas(str(output))
    pdf.drawString(72, 720, "Synthetic benchmark")
    pdf.save()
elif document_type == "docx":
    output = (OUTPUTS / "document.docx").resolve()
    source = """
const fs = require('fs');
const { Document, Packer, Paragraph, TextRun } = require('docx');
async function main() {
  const document = new Document({sections: [{
    properties: {page: {size: {width: 12240, height: 15840}}},
    children: [new Paragraph({children: [new TextRun('Synthetic benchmark')]})],
  }]});
  fs.writeFileSync(process.argv[2], await Packer.toBuffer(document));
}
main().catch((error) => { console.error(error); process.exit(1); });
"""
elif document_type == "pptx":
    output = (OUTPUTS / "document.pptx").resolve()
    source = """
const pptxgen = require('pptxgenjs');
async function main() {
  const presentation = new pptxgen();
  presentation.layout = 'LAYOUT_WIDE';
  const slide = presentation.addSlide();
  slide.addText('Synthetic benchmark', {x: 0.7, y: 0.7, w: 8.0, h: 0.5, fontSize: 24});
  await presentation.writeFile({fileName: process.argv[2]});
}
main().catch((error) => { console.error(error); process.exit(1); });
"""
elif document_type == "xlsx":
    output = (OUTPUTS / "document.xlsx").resolve()
    workbook = Workbook()
    sheet = workbook.active
    if sheet is None:
        raise ValueError("workbook has no active worksheet")
    sheet.title = "Benchmark"
    sheet["A1"] = "Synthetic benchmark"
    sheet["A2"] = 1
    sheet["A3"] = 2
    sheet["A4"] = "=SUM(A2:A3)"
    workbook.save(output)
else:
    raise ValueError("unsupported document benchmark type")

if document_type in {"docx", "pptx"}:
    with tempfile.TemporaryDirectory() as source_directory:
        node_source = Path(source_directory) / "generate.js"
        node_source.write_text(source, encoding="utf-8")
        subprocess.run(["node", str(node_source), str(output)], check=True, timeout=120)  # noqa: S603, S607

render_started = time.monotonic()
with tempfile.TemporaryDirectory() as render_directory:
    render_root = Path(render_directory)
    if document_type in {"docx", "pptx", "xlsx"}:
        subprocess.run(  # noqa: S603
            ["libreoffice", "--headless", "--convert-to", "pdf", "--outdir", str(render_root), str(output)],  # noqa: S607 - fixed local tool invocation in a test fixture
            check=True,
            stdout=subprocess.DEVNULL,
            timeout=120,
        )
        rendered_pdf = render_root / f"{output.stem}.pdf"
    else:
        rendered_pdf = output
    preview_base = render_root / "document-preview"
    subprocess.run(  # noqa: S603
        ["pdftoppm", "-png", "-f", "1", "-l", "1", "-singlefile", str(rendered_pdf), str(preview_base)],  # noqa: S607 - fixed local tool invocation in a test fixture
        check=True,
        stdout=subprocess.DEVNULL,
        timeout=120,
    )
    shutil.copyfile(preview_base.with_suffix(".png"), OUTPUTS / "document-preview.png")
render_ms = int((time.monotonic() - render_started) * 1000)

input_hashes = sorted(hashlib.sha256(path.read_bytes()).hexdigest() for path in INPUTS.iterdir() if path.is_file())
network_success = False
try:
    connection = socket.create_connection(("1.1.1.1", 53), timeout=1)
    connection.close()
    network_success = True
except OSError:
    pass
child = subprocess.Popen(  # noqa: S603 - trusted subprocess in a test fixture
    [
        sys.executable,
        "-c",
        f"import time; time.sleep({int(os.environ.get('EDA_BENCHMARK_CHILD_SECONDS', '30'))})",
    ],
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
print(
    json.dumps(
        {
            "inputHashes": input_hashes,
            "networkSuccess": network_success,
            "childPid": child.pid,
            "renderMs": render_ms,
        },
        separators=(",", ":"),
    )
)
