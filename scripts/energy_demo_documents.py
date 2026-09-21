"""Render fictional source narratives and an isolated, reference-only demo guide.

Run: .venv/bin/python scripts/energy_demo_documents.py --pack .artifacts/energy-demo
Existing files require --overwrite. Only documents/pdf, documents/images and the
two evaluation/demo-guide files are output destinations. No cloud operations.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PureWindowsPath
from typing import Any, cast
from xml.sax.saxutils import escape

import pydyf

DISCLAIMER = "FICTIONAL SYNTHETIC DEMO - NOT OPERATOR DATA"
PAGE_WIDTH = 595.28
PAGE_HEIGHT = 841.89
MARGIN = 48.0
BODY_WIDTH = PAGE_WIDTH - 2 * MARGIN
NAVY = (0.07, 0.16, 0.25)
TEAL = (0.0, 0.40, 0.42)
INK = (0.15, 0.20, 0.24)
WHITE = (1.0, 1.0, 1.0)
# Standard PDF Helvetica AFM widths, U+0020 through U+007E, in thousandths of an em.
REGULAR_WIDTHS = (
    278,
    278,
    355,
    556,
    556,
    889,
    667,
    191,
    333,
    333,
    389,
    584,
    278,
    333,
    278,
    278,
    556,
    556,
    556,
    556,
    556,
    556,
    556,
    556,
    556,
    556,
    278,
    278,
    584,
    584,
    584,
    556,
    1015,
    667,
    667,
    722,
    722,
    667,
    611,
    778,
    722,
    278,
    500,
    667,
    556,
    833,
    722,
    778,
    667,
    778,
    722,
    667,
    611,
    722,
    667,
    944,
    667,
    667,
    611,
    278,
    278,
    278,
    469,
    556,
    333,
    556,
    556,
    500,
    556,
    556,
    278,
    556,
    556,
    222,
    222,
    500,
    222,
    833,
    556,
    556,
    556,
    556,
    333,
    500,
    278,
    556,
    500,
    722,
    500,
    500,
    500,
    334,
    260,
    334,
    584,
)
BOLD_WIDTHS = (
    278,
    333,
    474,
    556,
    556,
    889,
    722,
    238,
    333,
    333,
    389,
    584,
    278,
    333,
    278,
    278,
    556,
    556,
    556,
    556,
    556,
    556,
    556,
    556,
    556,
    556,
    333,
    333,
    584,
    584,
    584,
    611,
    975,
    722,
    722,
    722,
    722,
    667,
    611,
    778,
    722,
    278,
    556,
    722,
    611,
    833,
    722,
    778,
    667,
    778,
    722,
    667,
    611,
    722,
    667,
    944,
    667,
    667,
    611,
    333,
    278,
    333,
    584,
    556,
    333,
    556,
    611,
    556,
    611,
    556,
    333,
    611,
    611,
    278,
    278,
    556,
    278,
    889,
    611,
    611,
    611,
    611,
    389,
    556,
    333,
    611,
    556,
    778,
    556,
    556,
    500,
    389,
    280,
    389,
    584,
)
TRANSLATIONS = str.maketrans(
    {
        "\u2013": "-",
        "\u2014": " - ",
        "\u2212": "-",
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2022": "*",
        "\u2026": "...",
        "\u2264": "<=",
        "\u2265": ">=",
        "\u00d7": "x",
        "\u2192": "->",
        "\u00b0": " deg ",
    }
)


@dataclass(frozen=True)
class Section:
    heading: str
    text: str


@dataclass(frozen=True)
class SourceDocument:
    document_id: str
    file_name: str
    title: str
    status: str
    effective_date: str
    supersedes: str | None
    well_ids: tuple[str, ...]
    sections: tuple[Section, ...]


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    question: str
    tools: tuple[str, ...]
    sql: str | None
    expected: list[Any]
    behavior: str
    document_ids: tuple[str, ...]


def ascii_text(value: str) -> str:
    """Normalize common scientific punctuation without silently dropping symbols."""
    value = unicodedata.normalize("NFKD", value.translate(TRANSLATIONS))
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = value.replace("\r\n", "\n").replace("\r", "\n").replace("\t", "    ")
    if any((ord(char) < 32 and char != "\n") or ord(char) > 126 for char in value):
        raise ValueError("Text contains unsupported non-ASCII characters or control characters")
    return value


def text_width(text: str, size: float, font: str = "F1") -> float:
    if font == "F3":
        return len(text) * size * 0.6
    widths = BOLD_WIDTHS if font == "F2" else REGULAR_WIDTHS
    return sum(widths[ord(char) - 32] for char in text) * size / 1000


def wrap_text(text: str, size: float, width: float = BODY_WIDTH, font: str = "F1") -> list[str]:
    """Wrap words, with character-level fallback for URLs and long identifiers."""
    lines: list[str] = []
    for paragraph in ascii_text(text).split("\n"):
        if not paragraph.strip():
            lines.append("")
            continue
        line = ""
        for word in paragraph.split():
            candidate = f"{line} {word}" if line else word
            if text_width(candidate, size, font) <= width:
                line = candidate
                continue
            if line:
                lines.append(line)
                line = ""
            chunk = ""
            for char in word:
                if text_width(char, size, font) > width:
                    raise ValueError("Column is too narrow for a single character")
                if text_width(chunk + char, size, font) > width:
                    lines.append(chunk)
                    chunk = ""
                chunk += char
            line = chunk
        if line:
            lines.append(line)
    return lines


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return cast("dict[str, Any]", value)


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonempty string")
    return ascii_text(value)


def _strings(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list")
    return tuple(_string(item, label) for item in cast("list[Any]", value))


def _read_list(path: Path) -> list[Any]:
    def reject_constant(value: str) -> Any:
        raise ValueError(f"Non-finite JSON number: {value}")

    def unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result

    value: Any = json.loads(
        path.read_text(encoding="utf-8"), parse_constant=reject_constant, object_pairs_hook=unique_keys
    )
    if not isinstance(value, list) or not value:
        raise ValueError(f"{path.name} must contain a nonempty JSON list")
    return cast("list[Any]", value)


def safe_pdf_name(value: Any) -> str:
    name = _string(value, "fileName")
    if name != value or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._ -]*\.pdf", name, re.IGNORECASE):
        raise ValueError("fileName must be a safe ASCII .pdf basename")
    if PureWindowsPath(name).is_reserved() or len(name) > 160:
        raise ValueError("fileName is reserved or too long")
    return name


def load_documents(path: Path) -> tuple[SourceDocument, ...]:
    documents: list[SourceDocument] = []
    ids: set[str] = set()
    names: set[str] = set()
    for value in _read_list(path):
        item = _object(value, "document")
        document_id = _string(item.get("documentId"), "documentId")
        name = safe_pdf_name(item.get("fileName"))
        if document_id in ids or name.casefold() in names:
            raise ValueError("Duplicate documentId or case-insensitive fileName")
        ids.add(document_id)
        names.add(name.casefold())
        effective = _string(item.get("effectiveDate"), "effectiveDate")
        datetime.fromisoformat(effective)
        supersedes = item.get("supersedesDocumentId")
        if supersedes is not None:
            supersedes = _string(supersedes, "supersedesDocumentId")
        sections = item.get("sections")
        if not isinstance(sections, list) or not sections:
            raise ValueError("Each document needs a nonempty sections list")
        parsed_sections: list[Section] = []
        for raw_section in cast("list[Any]", sections):
            section = _object(raw_section, "section")
            parsed_sections.append(
                Section(_string(section.get("heading"), "heading"), _string(section.get("text"), "text"))
            )
        documents.append(
            SourceDocument(
                document_id,
                name,
                _string(item.get("title"), "title"),
                _string(item.get("status"), "status"),
                effective,
                supersedes,
                _strings(item.get("wellIds"), "wellIds"),
                tuple(parsed_sections),
            )
        )
    for document in documents:
        if document.supersedes and (document.supersedes not in ids or document.supersedes == document.document_id):
            raise ValueError(f"Invalid supersedesDocumentId for {document.document_id}")
    by_id = {document.document_id: document for document in documents}
    for document in documents:
        visited: set[str] = set()
        current: str | None = document.document_id
        while current:
            if current in visited:
                raise ValueError("Cyclic supersedesDocumentId chain")
            visited.add(current)
            current = by_id[current].supersedes
    return tuple(documents)


def load_scenarios(path: Path, document_ids: set[str]) -> tuple[Scenario, ...]:
    scenarios: list[Scenario] = []
    ids: set[str] = set()
    for value in _read_list(path):
        item = _object(value, "scenario")
        scenario_id = _string(item.get("id"), "scenario id")
        if scenario_id in ids:
            raise ValueError("Duplicate scenario id")
        ids.add(scenario_id)
        expected = item.get("expected")
        if not isinstance(expected, list):
            raise ValueError(f"{scenario_id}: expected must contain the generator's row list")
        # Preflight all text and nested numbers before creating any output.
        ascii_text(json.dumps(expected, ensure_ascii=False, allow_nan=False))
        required = _strings(item.get("requiredDocumentIds"), "requiredDocumentIds")
        if set(required) - document_ids:
            raise ValueError(f"{scenario_id}: unknown requiredDocumentIds")
        sql = item.get("sql")
        if sql is not None:
            sql = _string(sql, "sql")
        scenarios.append(
            Scenario(
                scenario_id,
                _string(item.get("question"), "question"),
                _strings(item.get("requiredTools"), "requiredTools"),
                sql,
                cast("list[Any]", expected),
                _string(item.get("expectedBehavior"), "expectedBehavior"),
                required,
            )
        )
    return tuple(scenarios)


@dataclass(frozen=True)
class TextLine:
    text: str
    x: float
    y: float
    size: float
    font: str = "F1"
    color: tuple[float, float, float] = INK


@dataclass
class Layout:
    metadata: tuple[str, ...]
    pages: list[list[TextLine]] = field(default_factory=lambda: list[list[TextLine]]())
    locators: list[dict[str, Any]] = field(default_factory=lambda: list[dict[str, Any]]())
    y: float = 0
    top: float = 0

    def __post_init__(self) -> None:
        metadata_lines = [line for item in self.metadata for line in wrap_text(item, 8.0)]
        if len(metadata_lines) > 18:
            raise ValueError("Document metadata exceeds the 18-line page-header capacity")
        self.top = 749.0 - 11 * len(metadata_lines) - 19.0
        self.new_page()

    def new_page(self) -> None:
        lines = [
            TextLine(DISCLAIMER, MARGIN, 806, 10, "F2", WHITE),
            TextLine("INDONESIA UPSTREAM  /  SYNTHETIC REFERENCE", MARGIN, 778, 9, "F2", TEAL),
        ]
        y = 749.0
        for item in self.metadata:
            for text in wrap_text(item, 8):
                lines.append(TextLine(text, MARGIN, y, 8))
                y -= 11
        self.pages.append(lines)
        self.y = self.top

    def paragraph(
        self,
        text: str,
        *,
        size: float = 10,
        font: str = "F1",
        color: tuple[float, float, float] = INK,
    ) -> set[int]:
        pages: set[int] = set()
        for line in wrap_text(text, size, font=font):
            leading = size * 1.45
            if self.y < 65 + leading:
                self.new_page()
            if line:
                self.pages[-1].append(TextLine(line, MARGIN, self.y, size, font, color))
            pages.add(len(self.pages))
            self.y -= leading
        self.y -= 6
        return pages

    def section(self, heading: str, text: str, *, font: str = "F1") -> None:
        heading_lines = len(wrap_text(heading, 12, font="F2"))
        required_height = min(heading_lines * 17.4 + 40, self.top - 80)
        if self.y - required_height < 80:
            self.new_page()
        pages = self.paragraph(heading, size=12, font="F2", color=TEAL)
        pages.update(self.paragraph(text, font=font))
        ordered = sorted(pages)
        self.locators.append(
            {
                "sectionIndex": len(self.locators) + 1,
                "heading": heading,
                "pages": ordered,
                "pageStart": ordered[0],
                "pageEnd": ordered[-1],
                "locator": f"pp. {ordered[0]}-{ordered[-1]}" if len(ordered) > 1 else f"p. {ordered[0]}",
            }
        )


def render_pdf(layout: Layout, title: str, subject: str) -> bytes:
    document: Any = pydyf.PDF()
    fonts: dict[str, Any] = {}
    for key, base in (("F1", "Helvetica"), ("F2", "Helvetica-Bold"), ("F3", "Courier")):
        font: Any = pydyf.Dictionary(
            {"Type": "/Font", "Subtype": "/Type1", "BaseFont": f"/{base}", "Encoding": "/WinAnsiEncoding"}
        )
        document.add_object(font)
        fonts[key] = font.reference
    for page_number, page_lines in enumerate(layout.pages, 1):
        stream: Any = pydyf.Stream()
        stream.set_color_rgb(*NAVY)
        stream.rectangle(0, 791, PAGE_WIDTH, 37)
        stream.fill()
        stream.set_color_rgb(*TEAL)
        stream.rectangle(MARGIN, 57, BODY_WIDTH, 1)
        stream.fill()
        lines = [
            *page_lines,
            TextLine("Synthetic reference | cite document ID, section and page", MARGIN, 39, 8),
            TextLine(f"Page {page_number} / {len(layout.pages)}", PAGE_WIDTH - 110, 39, 8, "F2"),
        ]
        stream.begin_text()
        for line in lines:
            if line.x + text_width(line.text, line.size, line.font) > PAGE_WIDTH - 20:
                raise ValueError(f"Text would clip outside page: {line.text!r}")
            stream.set_color_rgb(*line.color)
            stream.set_font_size(line.font, line.size)
            stream.set_text_matrix(1, 0, 0, 1, line.x, line.y)
            encoded: Any = line.text.encode("ascii")
            stream.show_text(pydyf.String(encoded))
        stream.end_text()
        document.add_object(stream)
        document.add_page(
            pydyf.Dictionary(
                {
                    "Type": "/Page",
                    "Parent": document.pages.reference,
                    "Contents": stream.reference,
                    "MediaBox": pydyf.Array([0, 0, PAGE_WIDTH, PAGE_HEIGHT]),
                    "Resources": pydyf.Dictionary({"Font": pydyf.Dictionary(fonts)}),
                }
            )
        )
    document.catalog["PageLabels"] = pydyf.Dictionary(
        {"Nums": pydyf.Array([0, pydyf.Dictionary({"S": "/D", "St": 1})])}
    )
    document.info["Title"] = pydyf.String(ascii_text(title))
    document.info["Subject"] = pydyf.String(ascii_text(subject))
    document.info["Author"] = pydyf.String("Fictional Indonesia upstream demo")
    document.info["Creator"] = pydyf.String("energy_demo_documents / pydyf")
    output = io.BytesIO()
    document.write(output, version=b"1.7", identifier=False)
    return output.getvalue()


def render_source(document: SourceDocument) -> tuple[bytes, dict[str, Any]]:
    metadata = (
        f"Document ID: {document.document_id}",
        f"Effective date: {document.effective_date} | Status: {document.status}",
        f"Supersedes: {document.supersedes or 'none'}",
        f"Well IDs: {', '.join(document.well_ids) or 'not well-specific'}",
    )
    layout = Layout(metadata)
    layout.paragraph(document.title, size=18, font="F2", color=NAVY)
    for section in document.sections:
        layout.section(section.heading, section.text)
    data = render_pdf(layout, document.title, " | ".join(metadata))
    return data, {
        "fileName": document.file_name,
        "sha256": hashlib.sha256(data).hexdigest(),
        "pageCount": len(layout.pages),
        "sections": layout.locators,
        "effectiveDate": document.effective_date,
        "status": document.status,
        "supersedesDocumentId": document.supersedes,
        "wellIds": list(document.well_ids),
    }


EVIDENCE = (
    (
        "IPA 1994 - Petani horizontal well",
        "Public context: 3920 ft TVD; datum unspecified. Not an MD or TVDSS conversion and not a synthetic well log.",
        "https://www.ipa.or.id/en/publications/"
        "petani-horizontal-well-management-strategy-to-recover-hydrocarbon-in-menggala-formation",
    ),
    (
        "IPA 1995 - Central Sumatra formation pressures",
        "Public context: structural/stratigraphic fluid barriers and pressure compartments. "
        "Does not establish connectivity between our fictional wells.",
        "https://www.ipa.or.id/en/publications/central-sumatra-prospect-evaluation-structural-and-"
        "stratigraphic-fluid-barriers-and-hydrodynamic-systems-as-indicated-by-wireline-formation-pressures",
    ),
    (
        "IPA 2001 - Marginal Minas X-sand stimulation",
        "Study-specific context: porosity 20%, permeability <15 mD. Incremental oil is not baseline oil production.",
        "https://www.ipa.or.id/en/publications/propellant-stimulation-technique-provides-alternate-productivity-"
        "enhancement-with-cost-reduction-benefits-in-development-of-a-marginal-reservoir-at-minas-field",
    ),
    (
        "IPA 2009 - Gelam compartmentalization",
        "Public context: connectivity interpretations evolve. "
        "This publication is not evidence for our fictional wells.",
        "https://www.ipa.or.id/en/publications/"
        "a-reservoir-engineering-study-of-gelam-field-to-understand-compartmentalization",
    ),
    (
        "LEMIGAS 2022 - Talang Akar",
        "Study-specific screening thresholds, not universal reservoir or commercial criteria. "
        "Water saturation is not produced water cut. Journal metadata dates differ; citation uses volume year.",
        "https://journal.lemigas.esdm.go.id/index.php/SCOG/article/view/1258",
    ),
    (
        "IPA 1995 - Upper Cibulakan / LES-1",
        "Public context: marine sandstone 800-2000 m subsea. Combined five-zone tests are not production volumes.",
        "https://www.ipa.or.id/en/publications/systematic-application-of-seismic-amplitude-analysis-for-"
        "exploration-in-the-upper-cibulakan-formation-example-from-gas-discovery-les-1-offshore-northwest-java",
    ),
    (
        "IPA 1994 - Upper Cibulakan / U-11",
        "Public context: heterogeneous sandstone packages. Synthetic stratigraphy is an authored assumption.",
        "https://www.ipa.or.id/en/publications/a-sequence-stratigraphic-model-of-the-upper-cibulakan-"
        "sandstones-main-interval-offshore-northwest-java-basin-insights-from-u-11-well",
    ),
    (
        "Microsoft - Ontology semantic enrichment",
        "Descriptions and synonyms aid AI interpretation. Entity synonyms are supported; "
        "relationship metadata is not currently consumed by the public Data Agent.",
        "https://learn.microsoft.com/fabric/iq/ontology/how-to-add-semantic-enrichment",
    ),
    (
        "Microsoft - GQL performance",
        "Filter early, bound traversal hops and project scalar values. Guidance is not a latency measurement.",
        "https://learn.microsoft.com/fabric/graph/gql-query-performance",
    ),
    (
        "Microsoft - ADME analytics consumption zone",
        "ACZ preview exports Delta data for a Fabric shortcut path; do not describe this as native mirroring.",
        "https://learn.microsoft.com/azure/energy-data-services/how-to-connect-analytics-consumption-zone-to-fabric",
    ),
    (
        "EnergyAssetAgent-ADME - personal MIT walkthrough",
        "Personal example uses Copilot Studio, Fabric Data Agent and SharePoint. Not a Foundry IQ implementation.",
        "https://github.com/julianjmoreno/EnergyAssetAgent-ADME",
    ),
)


def guide_sections(
    scenarios: tuple[Scenario, ...],
    citations: dict[str, Any],
    live_graph_report: dict[str, Any] | None = None,
) -> list[Section]:
    pack_status = (
        "STATUS PACK BARU: OFFLINE. Tabel, ontology definition, PDF dan skema Indonesia adalah artefak "
        "lokal kandidat, bukan bukti bahwa pack sudah dimuat, diindeks, dideploy atau dapat ditanya melalui "
        "layanan live. Bukan observasi operator Indonesia dan bukan koneksi ADME langsung."
    )
    if live_graph_report is not None:
        pack_status = (
            "STATUS PACK BARU: SUMBER FABRIC TERMUAT. Laporan GQL sumber baru mencatat "
            f"{live_graph_report['passed']}/{live_graph_report['total']} kasus cocok dengan oracle pada "
            f"{live_graph_report['observedAt']}. Ini bukti query graph, bukan keberhasilan agen natural-language. "
            "PDF dan SVG tetap paket lokal; belum dijadikan knowledge source cloud. "
            "Bukan observasi operator Indonesia dan bukan koneksi ADME langsung."
        )
    sections = [
        Section(
            "01 / Cakupan dan batas kemampuan",
            "Demo fiktif: 3 lapangan, 12 sumur, 92 hari (1 Juni-31 Agustus 2026), as-of 1 September 2026.\n\n"
            f"{pack_status}\n\n"
            "STATUS LAYANAN LAMA: LIVE, masih Lamna sampai deployment baru diotorisasi. Jalur yang diperiksa "
            "adalah ontology MCP + direct GQL, BUKAN otomatis Fabric Data Agent + Foundry IQ. Core dinyatakan "
            "ready; featurepacks Fabric/Documents terkonfigurasi tetapi belum readiness-certified. "
            "Konfigurasi fitur tidak membuktikan akses sumber, retrieval dokumen, atau kesiapan end-to-end.\n\n"
            "Skenario berikut adalah oracle referensi dari generator, menunggu actual agent runs. "
            "Tidak ada klaim akurasi model atau latency agen end-to-end. Nilai referensi bukan hasil agen. "
            "Narasi, sumur, hubungan dan angka operasional adalah sintetis; publikasi hanya konteks geologi.",
        ),
        Section(
            "Checkpoint layanan lama / 14 September 2026",
            "Hasil pemeriksaan yang dilaporkan fasilitator; renderer ini tidak menghubungi cloud atau "
            "menjalankan ulang probe. Checkpoint layanan ini berbeda dari cutoff data sintetis 1 September.\n\n"
            "Lamna live: 4 hospitals, 20 departments, 414 rooms, 308 patients, 308 monitors, "
            "308 monitoring signals. Ini inventaris model lama, bukan aset energi Indonesia.\n\n"
            "Graph energi lama: 600 wells; country CO=47, EG=175, KZ=32, MY=259, NG=50, OM=37. "
            "Tidak ada country ID. Jangan memakai graph tersebut sebagai bukti ketersediaan 12 sumur "
            "Indonesia dari paket baru.\n\n"
            "Probe direct GQL pada endpoint yang diperiksa: GROUP BY dengan RETURN alias dan OPTIONAL MATCH "
            "berhasil. CASE WHEN menghasilkan error 42000 (not supported). Gunakan agregat terfilter "
            "terpisah; jangan mengasumsikan semua SQL oracle dapat dieksekusi sebagai GQL. Keberhasilan "
            "subset sintaks bukan pengukuran akurasi agen atau pengujian dataset Indonesia yang baru.\n\n"
            "Konfigurasi kandidat memakai anggaran schema 24000 karakter dengan tipe properti eksplisit; "
            "ini bukan budget token atau bukti deployment. config/energy-agent-instructions.txt adalah "
            "candidate policy, belum deployed/agent-evaluated. config/energy-research-evidence.json "
            "mencatat konteks publik dan caveat; bukan pengukuran sumur atau distribusi statistik tervalidasi.\n\n"
            "Sebelum demo live, verifikasi identitas sumber, freshness graph, akses dan retrieval aktual. "
            "Node metadata Document atau URL tidak membuktikan teks lengkap telah dibaca. Error query, "
            "akses hilang atau retrieval belum dikonfigurasi bukan hasil kosong; nyatakan batasnya tanpa "
            "mengisi jawaban dari oracle. Jangan unggah evaluation untuk membuat demo tampak berhasil.",
        ),
        Section(
            "02 / Urutan demo 10-15 menit",
            "00:00-02:00 - Inventaris sumber: tabel, grain, rentang waktu, dokumen, status dan provenance.\n"
            "02:00-05:00 - Agregat: volume produksi, pembobotan water cut, kelengkapan data.\n"
            "05:00-08:00 - Lintas sumur dan konteks: bounded graph hops; MD/TVD/TVDSS; well-test vs produksi.\n"
            "08:00-11:00 - Konflik dokumen: tanggal efektif, supersedes, kutipan ID/bagian/halaman.\n"
            "11:00-13:00 - Abstention: alokasi lapisan commingled, konektivitas tidak pasti, data yang hilang.\n"
            "13:00-15:00 - Cadangan tanya jawab dan pencatatan hasil aktual; jangan ganti hasil dengan oracle.\n\n"
            "Pilih skenario relevan dari daftar lengkap di bawah. SQL adalah referensi lokal, bukan perintah "
            "yang dijalankan renderer. Required tools menyatakan kebutuhan skenario, bukan bukti deployment.",
        ),
        Section(
            "03 / Aturan interpretasi",
            "Volume produksi adalah jumlah selama interval; well-test rate adalah laju pada kondisi uji dan "
            "tidak boleh dijumlahkan sebagai volume aktual tanpa durasi dan dasar yang sesuai.\n\n"
            "Untuk sumur minyak, water cut tertimbang = jumlah volume air / jumlah (volume minyak + air) "
            "pada populasi dan interval yang sama; jangan memakai rata-rata persentase harian. Jika "
            "denominator nol, hasil tidak terdefinisi. Gas-condensate memakai WGR/CGR bila tersedia, "
            "bukan water cut minyak yang diterapkan secara otomatis.\n\n"
            "Missing/null berbeda dari nol terukur; tampilkan cakupan dan gap, jangan isi missing dengan nol. "
            "MD adalah panjang sepanjang lintasan, TVD adalah kedalaman vertikal dari datum yang dinyatakan, "
            "TVDSS adalah kedalaman vertikal terhadap muka laut dengan konvensi tanda yang dinyatakan. "
            "Tanpa datum/elevasi referensi, jangan mengonversi secara implisit.\n\n"
            "Tidak ada alokasi produksi per lapisan untuk completion commingled tanpa bukti alokasi. "
            "Konektivitas adalah interpretasi dengan ketidakpastian, bukan fakta hanya karena formasi sama. "
            "Rincian numerik dan kondisi spesifik ada dalam expected rows, bukan asumsi tambahan renderer.",
        ),
        Section(
            "04 / Isolasi evaluasi - jangan unggah",
            "TRAINING / EVALUATION LEAKAGE WARNING: direktori evaluation tidak boleh diunggah ke sumber "
            "OneLake atau knowledge base. demo-guide.pdf, demo-guide.txt dan scenarios.json hanya untuk "
            "fasilitator/evaluator, bukan retrieval corpus. Jangan hardcode jawaban oracle ke prompts atau "
            "dokumen sumber. Unggah hanya sumber yang sudah ditinjau dan diotorisasi secara terpisah.\n\n"
            "Tidak ada foto operator, core photo, atau seismic acquisition image. Bila tersedia, gambar "
            "adalah skema sintetis berlabel dengan nilai dari tabel, bukan foto atau bukti akuisisi lapangan.",
        ),
    ]
    if live_graph_report is not None:
        sections.insert(
            1,
            Section(
                "Hasil GQL langsung pada sumber Indonesia",
                f"Laporan teramati: {live_graph_report['observedAt']}\n"
                f"Kasus cocok dengan oracle: {live_graph_report['passed']}/{live_graph_report['total']}\n\n"
                + "\n".join(
                    f"{result['id']}: cocok; {result['seconds']} detik (query graph saja)"
                    for result in live_graph_report["results"]
                )
                + "\n\nAplikasi tetap menunjuk Lamna; konfigurasi sumber aplikasi tidak diubah. "
                "Data-agent/Foundry IQ retrieval dan jawaban natural-language belum dibuktikan oleh laporan ini. "
                "Status kapasitas dan akses dapat berubah setelah waktu observasi; periksa kembali sebelum demo.",
            ),
        )
    inventory: list[str] = []
    for document_id, entry in citations.items():
        inventory.append(
            f"{document_id} | {entry['fileName']} | {entry['status']} | "
            f"effective {entry['effectiveDate']} | {entry['pageCount']} pages\n"
            f"SHA256: {entry['sha256']}\n"
            + "\n".join(f"  {section['heading']}: {section['locator']}" for section in entry["sections"])
        )
    sections.append(Section("05 / Inventaris dan peta sitasi dokumen", "\n\n".join(inventory)))
    for index, scenario in enumerate(scenarios, 1):
        sources: list[str] = []
        for document_id in scenario.document_ids:
            entry = citations[document_id]
            sources.append(
                f"{document_id} | {entry['fileName']}\n"
                + "\n".join(f"{section['heading']}: {section['locator']}" for section in entry["sections"])
            )
        sections.append(
            Section(
                f"Skenario {index:02d} / {scenario.scenario_id}",
                f"Pertanyaan: {scenario.question}\n\n"
                f"Required tools: {', '.join(scenario.tools) or '(none)'}\n\n"
                f"Expected behavior: {scenario.behavior}\n\n"
                "Sumber dokumen dan halaman:\n"
                + ("\n\n".join(sources) if sources else "Tidak ada requiredDocumentIds; gunakan sumber terstruktur.")
                + "\n\nExpected rows (oracle generator; bukan output agen):\n"
                + json.dumps(scenario.expected, ensure_ascii=False, allow_nan=False, indent=2)
                + ("\n\nSQL referensi:\n" + scenario.sql if scenario.sql else "\n\nSQL: tidak disediakan generator."),
            )
        )
    legend = "\n\n".join(f"{title}\n{description}\n{url}" for title, description, url in EVIDENCE)
    sections.append(Section("Legenda bukti publik vs asumsi sintetis", legend))
    return sections


def render_guide(
    scenarios: tuple[Scenario, ...],
    citations: dict[str, Any],
    live_graph_report: dict[str, Any] | None = None,
) -> tuple[bytes, bytes]:
    title = "Panduan demo Indonesia upstream - evaluasi terisolasi"
    layout = Layout(
        (
            "Document ID: DEMO-GUIDE | Status: evaluation only",
            "Effective date / as-of: 2026-09-01 | Supersedes: none | Well IDs: all 12 fictional wells",
            "DO NOT UPLOAD: evaluation oracle, not a retrieval source",
        )
    )
    layout.paragraph(title, size=18, font="F2", color=NAVY)
    text_parts = [DISCLAIMER, title, *layout.metadata]
    for section in guide_sections(scenarios, citations, live_graph_report):
        layout.section(section.heading, section.text)
        text_parts.extend((section.heading, section.text))
    return (
        render_pdf(layout, title, "EVALUATION ONLY - DO NOT UPLOAD TO RETRIEVAL"),
        ascii_text("\n\n".join(text_parts) + "\n").encode("ascii"),
    )


def _destination(pack: Path, relative: Path) -> Path:
    path = pack / relative
    current = path
    while current != pack:
        if current.is_symlink():
            raise ValueError(f"Refusing symlink output path: {relative}")
        current = current.parent
    if not path.resolve().is_relative_to(pack):
        raise ValueError(f"Output escapes pack: {relative}")
    if path.exists() and not path.is_file():
        raise ValueError(f"Output is not a regular file: {relative}")
    if path.exists() and path.stat().st_nlink > 1:
        raise ValueError(f"Refusing multiply linked output file: {relative}")
    return path


def _input(pack: Path, relative: Path) -> Path:
    path = pack / relative
    if not path.resolve(strict=True).is_relative_to(pack):
        raise ValueError(f"Input escapes pack: {relative}")
    return path


def render_depth_schematics(pack: Path) -> tuple[dict[Path, bytes], list[dict[str, Any]]]:
    """Draw values, not invented trajectories, using the published Wellbore schema."""
    if not (pack / "schema.json").exists():
        return {}, []
    schema = _object(json.loads(_input(pack, Path("schema.json")).read_text(encoding="utf-8")), "schema")
    tables = schema.get("tables")
    if not isinstance(tables, list):
        raise ValueError("schema.tables must be a list")
    wellbore = next(
        (
            table
            for raw in cast("list[Any]", tables)
            if (table := _object(raw, "schema table")).get("name") == "Wellbore"
        ),
        None,
    )
    if wellbore is None:
        return {}, []
    columns = wellbore.get("columns")
    if not isinstance(columns, list):
        raise ValueError("Wellbore.columns must be a list")
    declared = {_string(_object(column, "column").get("name"), "column.name") for column in cast("list[Any]", columns)}
    required = {
        "WellboreId",
        "WellId",
        "TotalDepthMdM",
        "TotalDepthTvdM",
        "TotalDepthTvdssM",
        "ReferenceElevationM",
        "DepthReference",
        "SurveyDate",
        "Synthetic",
    }
    if not required <= declared:
        raise ValueError("Wellbore schema lacks required depth, datum or provenance columns")
    path = _input(pack, Path("tables") / "Wellbore.csv")
    grouped: dict[str, list[dict[str, str]]] = {}
    seen: set[str] = set()
    depth_columns = ("TotalDepthMdM", "TotalDepthTvdM", "TotalDepthTvdssM")
    values: list[float] = [0.0]
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None or not required <= set(reader.fieldnames):
            raise ValueError("Wellbore.csv columns do not match its declared schema")
        for raw_row in reader:
            if None in raw_row or any(value is None for value in raw_row.values()):
                raise ValueError("Malformed Wellbore.csv row")
            row = {key: _string(raw_row[key], f"Wellbore.{key}") for key in required}
            if row["Synthetic"].lower() != "true":
                raise ValueError("Depth schematics require explicitly Synthetic=true rows")
            if row["WellboreId"] in seen:
                raise ValueError("Duplicate WellboreId")
            seen.add(row["WellboreId"])
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,80}", row["WellId"]):
                raise ValueError("WellId is unsafe for schematic filename")
            datetime.fromisoformat(row["SurveyDate"])
            for key in (*depth_columns, "ReferenceElevationM"):
                if not math.isfinite(float(row[key])):
                    raise ValueError("Depth schematic values must be finite")
            md, tvd, tvdss = (float(row[key]) for key in depth_columns)
            if md < tvd or tvd < 0:
                raise ValueError("Wellbore depths require MD >= TVD >= 0")
            if not math.isclose(tvd - float(row["ReferenceElevationM"]), tvdss, abs_tol=0.01):
                raise ValueError("Wellbore TVDSS disagrees with its stated drill-floor reference")
            values.extend((md, tvd, tvdss))
            grouped.setdefault(row["WellId"], []).append(row)
    if not grouped:
        raise ValueError("Declared Wellbore.csv has no data rows")
    low, high = min(values), max(values)
    span = high - low or 1.0
    zero_x = 235 + (0 - low) / span * 660
    outputs: dict[Path, bytes] = {}
    records: list[dict[str, Any]] = []
    for well_id, rows in sorted(grouped.items()):
        parts: list[str] = []

        def text(
            value: str,
            x: float,
            y: float,
            size: float = 16,
            color: str = "#26333d",
            *,
            _parts: list[str] = parts,
        ) -> None:
            if x + text_width(value, size) > 1060:
                raise ValueError("Schematic text would clip outside the image")
            _parts.append(
                f'<text x="{x:.2f}" y="{y:.2f}" font-family="Helvetica,Arial,sans-serif" '
                f'font-size="{size}" fill="{color}">{escape(value)}</text>'
            )

        def wrapped(value: str, y: float, size: float = 15) -> float:
            for line in wrap_text(value, size, width=990):
                text(line, 40, y, size)
                y += size * 1.4
            return y

        parts.append('<rect width="1100" height="56" fill="#12293f"/>')
        text(DISCLAIMER, 40, 35, 20, "#ffffff")
        y = wrapped(f"{well_id} / Wellbore depth comparison schematic", 99, 25)
        y = wrapped(
            "SCHEMATIC ONLY - Not a trajectory survey, seismic acquisition image, core photo or operator photograph.",
            y + 5,
        )
        y = wrapped("Metres; bars compare supplied depths, not lateral position or reservoir connectivity.", y + 7)
        for row in sorted(rows, key=lambda item: item["WellboreId"]):
            y = wrapped(f"Wellbore: {row['WellboreId']} | Survey: {row['SurveyDate']}", y + 28, 18)
            y = wrapped(
                f"Reference elevation: {row['ReferenceElevationM']} m above MSL | {row['DepthReference']}", y + 5
            )
            for key, label, color in (
                ("TotalDepthMdM", "MD from drill floor", "#12293f"),
                ("TotalDepthTvdM", "TVD from drill floor", "#00777c"),
                ("TotalDepthTvdssM", "TVDSS below MSL", "#527d86"),
            ):
                y += 33
                text(label, 40, y + 16, 15)
                end_x = 235 + (float(row[key]) - low) / span * 660
                parts.append(
                    f'<rect x="{min(zero_x, end_x):.2f}" y="{y:.2f}" '
                    f'width="{abs(end_x - zero_x):.2f}" height="22" fill="{color}"/>'
                )
                text(f"{row[key]} m", max(zero_x, end_x) + 10, y + 17, 15)
            y += 27
        y = wrapped(
            "Source: tables/Wellbore.csv; column meanings: schema.json. "
            "All values are authored synthetic observations, not geographic measurements.",
            y + 28,
        )
        y = wrapped(
            "TVDSS = TVD - stated reference elevation; positive below MSL. "
            "No completion allocation, actual survey trajectory or photograph is implied.",
            y + 8,
        )
        height = math.ceil(y + 25)
        svg = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="1100" height="{height}" '
            f'viewBox="0 0 1100 {height}" role="img">'
            f"<title>{escape(well_id)} - fictional synthetic depth schematic</title>"
            '<rect width="100%" height="100%" fill="#ffffff"/>' + "".join(parts) + "</svg>\n"
        ).encode("ascii")
        name = f"wellbore-depth-{well_id.lower()}.svg"
        if Path("documents") / "images" / name in outputs:
            raise ValueError("Case-insensitive duplicate schematic filename")
        outputs[Path("documents") / "images" / name] = svg
        records.append(
            {
                "fileName": name,
                "wellIds": [well_id],
                "sourceTable": "tables/Wellbore.csv",
                "columns": list(depth_columns),
                "schematicOnly": True,
                "sha256": hashlib.sha256(svg).hexdigest(),
            }
        )
    return outputs, records


def load_live_graph_report(path: Path) -> dict[str, Any]:
    report = _object(json.loads(path.read_text(encoding="utf-8")), "Live GQL report")
    total, passed = report.get("total"), report.get("passed")
    if type(total) is not int or total < 1 or type(passed) is not int or passed != total:
        raise ValueError("Live GQL report must contain a nonempty, fully passing run")
    raw_results = report.get("results")
    if not isinstance(raw_results, list):
        raise ValueError("Live GQL report results must be a list")
    results = [_object(value, "Live GQL case") for value in cast("list[Any]", raw_results)]
    if len(results) != total:
        raise ValueError("Live GQL report result count differs from total")
    for result in results:
        if result.get("pass") is not True or result.get("status") != "00000":
            raise ValueError("Live GQL report contains a failed case")
        _string(result.get("id"), "Live GQL case identity")
        if not isinstance(result.get("actual"), list):
            raise ValueError("Live GQL report lacks case identity or actual rows")
        seconds = result.get("seconds")
        if (
            isinstance(seconds, bool)
            or not isinstance(seconds, (int, float))
            or not math.isfinite(seconds)
            or seconds < 0
        ):
            raise ValueError("Live GQL report has invalid elapsed time")
    if len({result["id"] for result in results}) != total:
        raise ValueError("Live GQL report contains duplicate case identities")
    if not _string(report.get("scope"), "Live GQL scope").startswith("graph-only"):
        raise ValueError("Live GQL report must explicitly limit its scope to graph queries")
    observed_at = report.get("observedAt")
    if not isinstance(observed_at, str) or datetime.fromisoformat(observed_at).tzinfo is None:
        raise ValueError("Live GQL report must have a timezone-aware observation timestamp")
    return report


def build_pack(pack: Path, *, overwrite: bool = False, live_gql_report: Path | None = None) -> dict[str, Any]:
    pack = pack.resolve(strict=True)
    if not pack.is_dir():
        raise ValueError("--pack must be an existing directory")
    documents = load_documents(_input(pack, Path("documents") / "source-documents.json"))
    scenarios = load_scenarios(
        _input(pack, Path("evaluation") / "scenarios.json"), {doc.document_id for doc in documents}
    )
    live_report = load_live_graph_report(_input(pack, live_gql_report)) if live_gql_report is not None else None
    outputs, images = render_depth_schematics(pack)
    citations: dict[str, Any] = {}
    for document in documents:
        pdf, entry = render_source(document)
        outputs[Path("documents") / "pdf" / document.file_name] = pdf
        citations[document.document_id] = entry
    citation_map = {
        "schemaVersion": 1,
        "disclaimer": DISCLAIMER,
        "documents": citations,
        "images": images,
    }
    outputs[Path("documents") / "pdf" / "citation-map.json"] = (
        json.dumps(citation_map, ensure_ascii=True, indent=2) + "\n"
    ).encode("ascii")
    guide_pdf, guide_text = render_guide(scenarios, citations, live_report)
    outputs[Path("evaluation") / "demo-guide.pdf"] = guide_pdf
    outputs[Path("evaluation") / "demo-guide.txt"] = guide_text
    destinations = {relative: _destination(pack, relative) for relative in outputs}
    collisions = [str(relative) for relative, path in destinations.items() if path.exists()]
    if collisions and not overwrite:
        raise FileExistsError("Outputs already exist; use --overwrite: " + ", ".join(collisions))
    for relative, path in destinations.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb" if overwrite else "xb") as stream:
            stream.write(outputs[relative])
    return citation_map


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", type=Path, default=Path(".artifacts") / "energy-demo")
    parser.add_argument("--overwrite", action="store_true", help="Replace renderer-owned output files")
    parser.add_argument(
        "--live-gql-report", type=Path, help="Pack-relative operator-only report from energy_demo_live.py"
    )
    args = parser.parse_args(argv)
    try:
        result = build_pack(args.pack, overwrite=args.overwrite, live_gql_report=args.live_gql_report)
    except (OSError, ValueError) as exc:
        parser.exit(2, f"Document rendering failed: {exc}\n")
    print(
        f"Rendered {len(result['documents'])} source PDFs, {len(result['images'])} depth schematics "
        "and isolated evaluation/demo-guide.pdf + .txt"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
