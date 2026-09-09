from __future__ import annotations

import hashlib
import json
import os
import random
import subprocess
import time
import zipfile
from decimal import Decimal
from pathlib import Path

import openpyxl
import xlsxwriter

ROOT = Path.cwd()
INPUTS = ROOT / "inputs"
OUTPUTS = ROOT / "outputs"
OUTPUTS.mkdir(parents=True, exist_ok=True)
render_ms = 0

chromium = [
    "/usr/bin/chromium",
    "--headless",
    "--no-sandbox",
    "--disable-gpu",
    "--disable-dev-shm-usage",
    "--hide-scrollbars",
    "--no-zygote",
    "--renderer-process-limit=1",
]
mermaid_path = OUTPUTS / "revenue-flow.mmd"
mermaid_path.write_text(
    "flowchart LR\n  A[Input workbook] --> B[Decimal controls]\n  B --> C[Validated artifacts]\n",
    encoding="utf-8",
)
mermaid_environment = {
    **os.environ,
    "PUPPETEER_EXECUTABLE_PATH": "/usr/bin/chromium",
    "NODE_PATH": "/opt/eda/node_modules",
}
puppeteer_config = ROOT / "tmp" / "puppeteer-config.json"
puppeteer_config.parent.mkdir(exist_ok=True)
puppeteer_config.write_text(
    json.dumps(
        {
            "executablePath": "/usr/bin/chromium",
            "args": [
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--no-zygote",
                "--single-process",
                "--renderer-process-limit=1",
            ],
        },
        separators=(",", ":"),
    ),
    encoding="utf-8",
)
mermaid_config = ROOT / "tmp" / "mermaid-config.json"
mermaid_config.write_text(
    json.dumps(
        {"securityLevel": "strict", "htmlLabels": False},
        separators=(",", ":"),
    ),
    encoding="utf-8",
)
mermaid_svg = OUTPUTS / "revenue-flow.svg"
render_started = time.monotonic()
subprocess.run(  # noqa: S603 -- fixed Mermaid CLI, config, source, and output paths.
    [
        "/usr/local/bin/mmdc",
        "-p",
        str(puppeteer_config),
        "-c",
        str(mermaid_config),
        "-i",
        str(mermaid_path),
        "-o",
        str(mermaid_svg),
        "-b",
        "transparent",
    ],
    check=True,
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    timeout=90,
    env=mermaid_environment,
)
render_ms += int((time.monotonic() - render_started) * 1000)
render_started = time.monotonic()
subprocess.run(  # noqa: S603 -- fixed Chromium argv and generated local SVG URI.
    [
        *chromium,
        "--window-size=1200,600",
        f"--screenshot={OUTPUTS / 'revenue-flow.png'}",
        mermaid_svg.resolve().as_uri(),
    ],
    check=True,
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    timeout=60,
)
render_ms += int((time.monotonic() - render_started) * 1000)

import matplotlib  # noqa: E402

matplotlib.use("Agg")
from matplotlib import pyplot as plt  # noqa: E402

parameters = json.loads(next(INPUTS.glob("*.json")).read_text(encoding="utf-8"))
random.seed(parameters["randomSeed"])
input_path = next(INPUTS.glob("*.xlsx"))
input_book = openpyxl.load_workbook(input_path, data_only=True, read_only=True)
sheet = input_book["Revenue"]
rows = [(str(region), Decimal(str(revenue))) for region, revenue in sheet.iter_rows(min_row=2, values_only=True)]
control_total = sum((revenue for _, revenue in rows), Decimal("0"))

workbook_path = OUTPUTS / "analysis.xlsx"
workbook = xlsxwriter.Workbook(workbook_path)
money = workbook.add_format({"num_format": "$#,##0.00"})
data_sheet = workbook.add_worksheet("Data")
data_sheet.write_row(0, 0, ["Region", "Revenue"])
for row_index, (region, revenue) in enumerate(rows, start=1):
    data_sheet.write(row_index, 0, region)
    data_sheet.write_number(row_index, 1, float(revenue), money)
summary_sheet = workbook.add_worksheet("Summary")
summary_sheet.write("A1", "FY2026 Revenue")
summary_sheet.write("A2", "Control total")
summary_sheet.write_formula("B2", f"=SUM(Data!B2:B{len(rows) + 1})", money, float(control_total))
summary_sheet.write("A4", "Formula cells are cached for deterministic validation.")
workbook.close()

html_path = OUTPUTS / "report.html"
row_markup = "".join(f"<tr><td>{region}</td><td>${revenue:,.2f}</td></tr>" for region, revenue in rows)
html_path.write_text(
    "<!doctype html><html><head><meta charset='utf-8'><title>FY2026 Revenue</title>"
    "<style>body{font-family:sans-serif;margin:24px;color:#17212b}table{border-collapse:collapse}"
    "td,th{border:1px solid #657786;padding:8px 12px;text-align:right}td:first-child{text-align:left}"
    "h1{font-size:24px}@media(max-width:500px){body{margin:12px}h1{font-size:20px}}</style></head>"
    f"<body><h1>FY2026 Revenue</h1><p id='control'>Control total: ${control_total:,.2f}</p>"
    f"<table><thead><tr><th>Region</th><th>Revenue</th></tr></thead><tbody>{row_markup}</tbody></table>"
    "</body></html>",
    encoding="utf-8",
)

regions = [region for region, _ in rows]
values = [float(revenue) for _, revenue in rows]
fig, axis = plt.subplots(figsize=(8, 4.5), dpi=120)
axis.bar(regions, values, color=["#2364aa", "#3da35d", "#f6ae2d"])
axis.set_title("FY2026 Revenue by Region")
axis.set_ylabel("USD")
fig.tight_layout()
fig.savefig(OUTPUTS / "revenue-chart.png")
fig.savefig(OUTPUTS / "revenue-chart.svg")
plt.close(fig)

for name, dimensions in (("report-desktop.png", "1280,720"), ("report-mobile.png", "390,844")):
    render_started = time.monotonic()
    subprocess.run(  # noqa: S603 -- fixed Chromium argv and generated local HTML URI.
        [*chromium, f"--window-size={dimensions}", f"--screenshot={OUTPUTS / name}", html_path.resolve().as_uri()],
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=60,
    )
    render_ms += int((time.monotonic() - render_started) * 1000)

source_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
input_hash = hashlib.sha256(input_path.read_bytes()).hexdigest()
execution_id = f"exec_{Path(__file__).stem}"
artifact_names = [
    "analysis.xlsx",
    "report.html",
    "report-desktop.png",
    "report-mobile.png",
    "revenue-chart.svg",
    "revenue-chart.png",
    "revenue-flow.mmd",
    "revenue-flow.svg",
    "revenue-flow.png",
]
artifacts = [
    {"name": name, "sha256": hashlib.sha256((OUTPUTS / name).read_bytes()).hexdigest()} for name in artifact_names
]
manifest = {
    "schemaVersion": "1.0",
    "artifacts": artifacts,
    "runtime": {
        "scriptSha256": source_hash,
        "parameters": parameters,
        "imageDigest": parameters["imageDigest"],
        "executionId": execution_id,
        "randomSeed": parameters["randomSeed"],
    },
    "input": {
        "sha256": input_hash,
        "rows": len(rows),
        "nulls": 0,
        "duplicates": 0,
        "decimalHandling": "decimal-string-roundtrip",
        "controlTotal": str(control_total),
    },
    "claims": [
        {
            "text": f"FY2026 revenue totals ${control_total:,.2f}.",
            "important": True,
            "evidenceRefs": [
                f"input:{input_hash}",
                f"execution:{execution_id}",
                f"output:analysis.xlsx:{artifacts[0]['sha256']}",
            ],
        }
    ],
}
manifest_path = OUTPUTS / "task-manifest.json"
manifest_bytes = (json.dumps(manifest, ensure_ascii=True, separators=(",", ":"), sort_keys=True) + "\n").encode()
manifest_path.write_bytes(manifest_bytes)

bundle_path = OUTPUTS / "reproducibility.zip"
with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
    for name, body in (
        ("generate_outputs.py", Path(__file__).read_bytes()),
        ("task-manifest.json", manifest_bytes),
        ("parameters.json", (json.dumps(parameters, sort_keys=True, separators=(",", ":")) + "\n").encode()),
    ):
        info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o600 << 16
        bundle.writestr(info, body)

print(
    json.dumps(
        {"status": "complete", "controlTotal": str(control_total), "renderMs": render_ms},
        separators=(",", ":"),
    )
)
