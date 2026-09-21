from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import shutil
import sys
import uuid
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("energy_demo_documents", ROOT / "scripts" / "energy_demo_documents.py")
assert SPEC is not None and SPEC.loader is not None
renderer = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = renderer
SPEC.loader.exec_module(renderer)


@pytest.fixture
def pack() -> Iterator[Path]:
    # Keep fixtures inside the owned generated-output tree, never system temp.
    path = ROOT / ".artifacts" / "energy-demo" / "documents" / "images" / f"test-{uuid.uuid4().hex}"
    (path / "documents").mkdir(parents=True)
    (path / "evaluation").mkdir()
    documents: list[dict[str, Any]] = [
        {
            "documentId": "DOC-A",
            "fileName": "synthetic-review.pdf",
            "title": "Tinjauan sintetis (bukan data operator)",
            "status": "current",
            "effectiveDate": "2026-09-01",
            "supersedesDocumentId": None,
            "wellIds": ["SYN-01", "SYN-02"],
            "sections": [
                {"heading": "Konteks", "text": "Narasi asli fiktif. Missing bukan nol.\n\nTekanan belum pasti."},
                {"heading": "Uraian panjang", "text": ("Panjang lintasan MD berbeda dari TVD. " * 300)},
                {"heading": "Token panjang", "text": "W" * 900 + "\nhttps://example.invalid/" + "x" * 1000},
            ],
        },
    ]
    scenarios = [
        {
            "id": "REF-01",
            "question": "Berapa total volume sintetis?",
            "requiredTools": ["sql", "document_search"],
            "sql": "SELECT 'EVALUATION_ONLY_TOKEN', 12345.6789",
            "expected": [{"volume": 12345.6789, "missing": None, "zero": 0, "details": {"coverage": 0.875}}],
            "expectedBehavior": "Beri kutipan. EVALUATION_ONLY_TOKEN",
            "requiredDocumentIds": ["DOC-A"],
        },
    ]
    (path / "documents" / "source-documents.json").write_text(json.dumps(documents), encoding="utf-8")
    (path / "evaluation" / "scenarios.json").write_text(json.dumps(scenarios), encoding="utf-8")
    try:
        yield path
    finally:
        shutil.rmtree(path)


def change_input(pack: Path, relative: str, update: Any) -> None:
    path = pack / relative
    data = json.loads(path.read_text(encoding="utf-8"))
    update(data)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_searchable_paginated_deterministic_isolated_outputs(pack: Path) -> None:
    result = renderer.build_pack(pack)
    entry = result["documents"]["DOC-A"]
    source_path = pack / "documents" / "pdf" / entry["fileName"]
    first = source_path.read_bytes()
    reader = PdfReader(io.BytesIO(first), strict=True)
    assert len(reader.pages) >= 4
    assert reader.page_labels == [str(index) for index in range(1, len(reader.pages) + 1)]
    for index, page in enumerate(reader.pages, 1):
        text = page.extract_text()
        assert renderer.DISCLAIMER in text
        assert f"Page {index} / {len(reader.pages)}" in text
        for metadata in ("DOC-A", "2026-09-01", "current", "Supersedes: none", "SYN-01", "SYN-02"):
            assert metadata in text
        assert "EVALUATION_ONLY_TOKEN" not in text
        assert "12345.6789" not in text
        assert "Expected rows" not in text
    assert entry["sha256"] == hashlib.sha256(first).hexdigest()
    for section in entry["sections"]:
        assert section["pageStart"] == section["pages"][0]
        assert section["pageEnd"] == section["pages"][-1]
        assert section["heading"] in reader.pages[section["pageStart"] - 1].extract_text()
    guide_path = pack / "evaluation" / "demo-guide.pdf"
    guide = guide_path.read_bytes()
    guide_reader = PdfReader(io.BytesIO(guide), strict=True)
    guide_text = "\n".join(page.extract_text() for page in guide_reader.pages)
    plain_text = (pack / "evaluation" / "demo-guide.txt").read_text(encoding="ascii")
    for text in (plain_text, guide_text):
        for expected in (
            "12345.6789",
            "0.875",
            "null",
            "EVALUATION_ONLY_TOKEN",
            "Lamna",
            "direct GQL",
            "Foundry IQ",
            "92 hari",
            "Required tools",
            "LEAKAGE",
            "OneLake",
            "3920 ft TVD",
        ):
            assert expected in text
    for page in guide_reader.pages:
        assert renderer.DISCLAIMER in page.extract_text()
    with pytest.raises(FileExistsError, match="overwrite"):
        renderer.build_pack(pack)
    assert source_path.read_bytes() == first
    assert renderer.build_pack(pack, overwrite=True) == result
    assert source_path.read_bytes() == first
    assert guide_path.read_bytes() == guide
    assert json.loads((source_path.parent / "citation-map.json").read_text()) == result
    assert not (source_path.parent / "demo-guide.pdf").exists()


def test_long_tokens_stay_within_page_and_preserve_content() -> None:
    for font in ("F1", "F2", "F3"):
        value = "W" * 2500 + " @" + "https://example.invalid/" * 60
        lines = renderer.wrap_text(value, 12, font=font)
        assert "".join(lines).replace(" ", "") == value.replace(" ", "")
        assert all(renderer.text_width(line, 12, font) <= renderer.BODY_WIDTH for line in lines)
    doc = renderer.SourceDocument(
        "D" * 350,
        "long.pdf",
        "W" * 900,
        "approved",
        "2026-09-01",
        None,
        ("WELL" * 70,),
        (renderer.Section("H" * 800, "W" * 5000),),
    )
    data, _ = renderer.render_source(doc)
    reader = PdfReader(io.BytesIO(data), strict=True)
    for page in reader.pages:

        def check(text: str, _cm: Any, tm: Any, font: Any, size: float) -> None:
            if not text:
                return
            key = "F2" if "Bold" in str(font.get("/BaseFont")) else "F1"
            for line in text.splitlines():
                if line:
                    assert tm[4] >= renderer.MARGIN
                    assert tm[4] + renderer.text_width(line, size, key) <= renderer.PAGE_WIDTH - 20
                    assert 30 <= tm[5] <= 820

        page.extract_text(visitor_text=check)


@pytest.mark.parametrize(
    "name",
    [
        "../bad.pdf",
        "/bad.pdf",
        r"..\bad.pdf",
        r"C:\bad.pdf",
        "bad.txt",
        "bad.pdf:stream",
        "CON.pdf",
        "a\n.pdf",
        ".pdf",
        "x" * 170 + ".pdf",
    ],
)
def test_unsafe_filename_rejected(pack: Path, name: str) -> None:
    change_input(pack, "documents/source-documents.json", lambda data: data[0].update(fileName=name))
    with pytest.raises(ValueError):
        renderer.build_pack(pack)
    assert not (pack / "documents" / "pdf").exists()
    assert not (pack / "evaluation" / "demo-guide.pdf").exists()


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("sections", [], "sections"),
        ("effectiveDate", "bad-date", "Invalid"),
        ("supersedesDocumentId", "MISSING", "supersedes"),
        ("wellIds", "SYN-01", "list"),
        ("title", "unsupported \u4e2d", "unsupported"),
    ],
)
def test_invalid_documents_fail_before_write(pack: Path, field: str, value: Any, match: str) -> None:
    change_input(pack, "documents/source-documents.json", lambda data: data[0].update({field: value}))
    with pytest.raises(ValueError, match=match):
        renderer.build_pack(pack)
    assert not (pack / "documents" / "pdf").exists()


def test_unknown_citation_and_malformed_oracle_fail_before_write(pack: Path) -> None:
    change_input(pack, "evaluation/scenarios.json", lambda data: data[0].update(requiredDocumentIds=["UNKNOWN"]))
    with pytest.raises(ValueError, match="unknown requiredDocumentIds"):
        renderer.build_pack(pack)
    change_input(pack, "evaluation/scenarios.json", lambda data: data[0].update(requiredDocumentIds=[], expected={}))
    with pytest.raises(ValueError, match="row list"):
        renderer.build_pack(pack)
    assert not (pack / "documents" / "pdf").exists()


def test_overwrite_preflight_preserves_existing_guide(pack: Path) -> None:
    guide = pack / "evaluation" / "demo-guide.txt"
    guide.write_text("existing content", encoding="ascii")
    with pytest.raises(FileExistsError):
        renderer.build_pack(pack)
    assert guide.read_text() == "existing content"
    assert not (pack / "documents" / "pdf").exists()


def test_symlink_output_directory_is_rejected(pack: Path) -> None:
    other = pack / "elsewhere"
    other.mkdir()
    (pack / "documents" / "pdf").symlink_to(other, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        renderer.build_pack(pack, overwrite=True)
    assert list(other.iterdir()) == []


def test_hardlinked_output_is_rejected_without_modifying_input(pack: Path) -> None:
    source = pack / "documents" / "source-documents.json"
    original = source.read_bytes()
    output = pack / "documents" / "pdf" / "synthetic-review.pdf"
    output.parent.mkdir()
    output.hardlink_to(source)
    with pytest.raises(ValueError, match="multiply linked"):
        renderer.build_pack(pack, overwrite=True)
    assert source.read_bytes() == original


def test_duplicate_filename_is_case_insensitive(pack: Path) -> None:
    def duplicate(data: Any) -> None:
        data.append({**data[0], "documentId": "DOC-B", "fileName": data[0]["fileName"].upper()})

    change_input(pack, "documents/source-documents.json", duplicate)
    with pytest.raises(ValueError, match="Duplicate"):
        renderer.build_pack(pack)


def test_supersession_cycles_are_rejected(pack: Path) -> None:
    def cycle(data: Any) -> None:
        data[0]["supersedesDocumentId"] = "DOC-B"
        data.append({**data[0], "documentId": "DOC-B", "fileName": "b.pdf", "supersedesDocumentId": "DOC-A"})

    change_input(pack, "documents/source-documents.json", cycle)
    with pytest.raises(ValueError, match="Cyclic"):
        renderer.build_pack(pack)
    assert not (pack / "documents" / "pdf").exists()


def test_json_duplicate_keys_are_rejected(pack: Path) -> None:
    (pack / "documents" / "source-documents.json").write_text('[{"documentId":"A","documentId":"B"}]')
    with pytest.raises(ValueError, match="Duplicate JSON key"):
        renderer.build_pack(pack)


def test_nonfinite_oracle_rejected(pack: Path) -> None:
    change_input(pack, "evaluation/scenarios.json", lambda data: data[0].update(expected=[{"value": float("inf")}]))
    with pytest.raises(ValueError, match="Non-finite"):
        renderer.build_pack(pack)
    assert not (pack / "documents" / "pdf").exists()


def test_every_reference_row_is_in_guide(pack: Path) -> None:
    def many_rows(data: Any) -> None:
        data[0]["expected"] = [{"key": f"UNIQUE_ORACLE_ROW_{index:03d}", "value": index / 8} for index in range(200)]

    change_input(pack, "evaluation/scenarios.json", many_rows)
    renderer.build_pack(pack)
    pdf_text = "\n".join(page.extract_text() for page in PdfReader(pack / "evaluation" / "demo-guide.pdf").pages)
    plain = (pack / "evaluation" / "demo-guide.txt").read_text()
    for index in range(200):
        assert f"UNIQUE_ORACLE_ROW_{index:03d}" in plain
        assert f"UNIQUE_ORACLE_ROW_{index:03d}" in pdf_text


def add_depth_table(pack: Path, *, md: str = "1500", synthetic: str = "true") -> None:
    names = [
        "WellboreId",
        "WellId",
        "TotalDepthMdM",
        "TotalDepthTvdM",
        "TotalDepthTvdssM",
        "ReferenceElevationM",
        "DepthReference",
        "SurveyDate",
        "Synthetic",
    ]
    schema = {"tables": [{"name": "Wellbore", "columns": [{"name": name} for name in names]}]}
    (pack / "schema.json").write_text(json.dumps(schema), encoding="utf-8")
    (pack / "tables").mkdir()
    values = [
        "SYN-01-M",
        "SYN-01",
        md,
        "1320",
        "1285",
        "35",
        "Synthetic drill floor above MSL",
        "2026-05-15T00:00:00Z",
        synthetic,
    ]
    (pack / "tables" / "Wellbore.csv").write_text(",".join(names) + "\n" + ",".join(values) + "\n")


def test_schematic_uses_actual_depths_and_is_truthfully_labeled(pack: Path) -> None:
    add_depth_table(pack)
    result = renderer.build_pack(pack)
    assert len(result["images"]) == 1
    record = result["images"][0]
    path = pack / "documents" / "images" / record["fileName"]
    data = path.read_bytes()
    root = ET.fromstring(data)  # noqa: S314 - Locally generated XML from a fixed synthetic test fixture.
    text = " ".join(root.itertext())
    for label in (
        renderer.DISCLAIMER,
        "SCHEMATIC ONLY",
        "1500 m",
        "1320 m",
        "1285 m",
        "35 m",
        "not geographic measurements",
        "not lateral position",
        "not",
        "photograph",
    ):
        assert label in text
    assert record["sha256"] == hashlib.sha256(data).hexdigest()
    assert record["sourceTable"] == "tables/Wellbore.csv"
    assert record["schematicOnly"] is True
    assert "EVALUATION_ONLY_TOKEN" not in text
    assert renderer.build_pack(pack, overwrite=True) == result
    assert path.read_bytes() == data


@pytest.mark.parametrize(
    ("md", "synthetic", "error"),
    [
        ("not-a-number", "true", "convert"),
        ("inf", "true", "finite"),
        ("100", "true", "MD"),
        ("1500", "false", "Synthetic"),
    ],
)
def test_schematic_invalid_data_is_explicit_error(pack: Path, md: str, synthetic: str, error: str) -> None:
    add_depth_table(pack, md=md, synthetic=synthetic)
    with pytest.raises(ValueError, match=error):
        renderer.build_pack(pack)
    assert not (pack / "documents" / "pdf").exists()


def test_declared_but_missing_wellbore_table_is_error(pack: Path) -> None:
    add_depth_table(pack)
    (pack / "tables" / "Wellbore.csv").unlink()
    with pytest.raises(FileNotFoundError):
        renderer.build_pack(pack)


def test_ascii_normalization_and_explicit_rejection() -> None:
    assert renderer.ascii_text("porosité \u2264 20% \u2014 2\u00b0C") == "porosite <= 20%  -  2 deg C"
    with pytest.raises(ValueError, match="unsupported"):
        renderer.ascii_text("invalid\x00")
    with pytest.raises(ValueError, match="narrow"):
        renderer.wrap_text("W", 12, width=1)


def test_effective_timestamp_supported(pack: Path) -> None:
    change_input(
        pack, "documents/source-documents.json", lambda data: data[0].update(effectiveDate="2026-06-01T00:00:00Z")
    )
    result = renderer.build_pack(pack)
    assert result["documents"]["DOC-A"]["effectiveDate"] == "2026-06-01T00:00:00Z"


def test_guide_distinguishes_legacy_live_service_from_offline_pack(pack: Path) -> None:
    renderer.build_pack(pack)
    plain = (pack / "evaluation" / "demo-guide.txt").read_text()
    pdf_text = "\n".join(page.extract_text() for page in PdfReader(pack / "evaluation" / "demo-guide.pdf").pages)
    for output in (plain, pdf_text):
        output = " ".join(output.split())
        for expected in (
            "STATUS PACK BARU: OFFLINE",
            "STATUS LAYANAN LAMA: LIVE",
            "belum readiness-certified",
            "4 hospitals",
            "414 rooms",
            "308 monitoring signals",
            "600 wells",
            "CO=47",
            "MY=259",
            "Tidak ada country ID",
            "OPTIONAL MATCH",
            "CASE WHEN",
            "42000 (not supported)",
            "24000 karakter",
            "candidate policy",
        ):
            assert expected in output
    source = PdfReader(pack / "documents" / "pdf" / "synthetic-review.pdf")
    source_text = "\n".join(page.extract_text() for page in source.pages)
    assert "600 wells" not in source_text
    assert "STATUS LAYANAN LAMA" not in source_text


def test_cli_error_is_actionable(pack: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (pack / "evaluation" / "scenarios.json").unlink()
    with pytest.raises(SystemExit) as error:
        renderer.main(["--pack", str(pack)])
    assert error.value.code == 2
    assert "Document rendering failed" in capsys.readouterr().err


def live_report() -> dict[str, Any]:
    return {
        "scope": "graph-only; not end-to-end agent correctness",
        "observedAt": "2026-09-14T22:51:26+00:00",
        "passed": 1,
        "total": 1,
        "results": [{"id": "live-case", "status": "00000", "pass": True, "actual": [{"n": 1}], "seconds": 1.5}],
    }


def test_live_report_updates_only_the_operator_guide(pack: Path) -> None:
    renderer.build_pack(pack)
    source = pack / "documents" / "pdf" / "synthetic-review.pdf"
    before = source.read_bytes()
    report_path = pack / "evaluation" / "live.json"
    report_path.write_text(json.dumps(live_report()))
    renderer.build_pack(pack, overwrite=True, live_gql_report=Path("evaluation") / "live.json")
    guide = (pack / "evaluation" / "demo-guide.txt").read_text()
    assert "STATUS PACK BARU: SUMBER FABRIC TERMUAT" in guide
    assert "STATUS PACK BARU: OFFLINE" not in guide
    assert "1/1" in guide
    assert "live-case: cocok" in guide
    assert "Aplikasi tetap menunjuk Lamna" in guide
    assert "belum dijadikan knowledge source cloud" in guide
    assert source.read_bytes() == before


@pytest.mark.parametrize(
    "changes",
    [
        {"total": 0, "passed": 0, "results": []},
        {"passed": 0},
        {"total": 2},
        {"scope": "end-to-end"},
        {"observedAt": "2026-09-15T05:51:26"},
        {"results": [{"id": "failed", "status": "42000", "pass": True, "actual": [], "seconds": 1.0}]},
    ],
)
def test_invalid_live_report_cannot_promote_guide_status(pack: Path, changes: dict[str, Any]) -> None:
    report = live_report() | changes
    path = pack / "evaluation" / "invalid-live.json"
    path.write_text(json.dumps(report))
    with pytest.raises(ValueError):
        renderer.build_pack(pack, live_gql_report=Path("evaluation") / "invalid-live.json")
    assert not (pack / "evaluation" / "demo-guide.pdf").exists()
