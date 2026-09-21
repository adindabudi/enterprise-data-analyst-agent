from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from typing import Any

import pydyf

from .office import validate_office
from .pdf import PageRenderer, validate_pdf

ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
CONTENT_TYPES = "http://schemas.openxmlformats.org/package/2006/content-types"
RELATIONSHIPS = "http://schemas.openxmlformats.org/package/2006/relationships"
OFFICE_RELATIONSHIPS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


@dataclass(frozen=True)
class GeneratedDocument:
    kind: str
    display_name: str
    content: bytes


def generate_document_corpus(title: str = "Enterprise Data Analyst") -> tuple[GeneratedDocument, ...]:
    return (
        GeneratedDocument("pptx", "analysis.pptx", generate_pptx(title)),
        GeneratedDocument("docx", "analysis.docx", generate_docx(title)),
        GeneratedDocument("xlsx", "analysis.xlsx", generate_xlsx(title)),
        GeneratedDocument("pdf", "analysis.pdf", generate_pdf(title)),
    )


def generate_pptx(title: str) -> bytes:
    members = {
        "[Content_Types].xml": f"""<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="{CONTENT_TYPES}">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>
<Override PartName="/ppt/slides/slide1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>
</Types>""",
        "_rels/.rels": f"""<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="{RELATIONSHIPS}"><Relationship Id="rId1" Type="{OFFICE_RELATIONSHIPS}/officeDocument" Target="ppt/presentation.xml"/></Relationships>""",
        "ppt/presentation.xml": f"""<?xml version="1.0" encoding="UTF-8"?>
<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:r="{OFFICE_RELATIONSHIPS}"><p:sldIdLst><p:sldId id="256" r:id="rId1"/></p:sldIdLst><p:sldSz cx="12192000" cy="6858000"/></p:presentation>""",
        "ppt/_rels/presentation.xml.rels": f"""<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="{RELATIONSHIPS}"><Relationship Id="rId1" Type="{OFFICE_RELATIONSHIPS}/slide" Target="slides/slide1.xml"/></Relationships>""",
        "ppt/slides/slide1.xml": f"""<?xml version="1.0" encoding="UTF-8"?>
<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><p:cSld><p:spTree><p:nvGrpSpPr/><p:grpSpPr/><p:sp><p:nvSpPr/><p:spPr/><p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:t>{_xml(title)}</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>""",
    }
    return _zip(members)


def generate_docx(title: str) -> bytes:
    members = {
        "[Content_Types].xml": f"""<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="{CONTENT_TYPES}"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>""",
        "_rels/.rels": f"""<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="{RELATIONSHIPS}"><Relationship Id="rId1" Type="{OFFICE_RELATIONSHIPS}/officeDocument" Target="word/document.xml"/></Relationships>""",
        "word/document.xml": f"""<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>{_xml(title)}</w:t></w:r></w:p><w:sectPr><w:pgSz w:w="12240" w:h="15840"/></w:sectPr></w:body></w:document>""",
    }
    return _zip(members)


def generate_xlsx(title: str) -> bytes:
    members = {
        "[Content_Types].xml": f"""<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="{CONTENT_TYPES}"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>""",
        "_rels/.rels": f"""<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="{RELATIONSHIPS}"><Relationship Id="rId1" Type="{OFFICE_RELATIONSHIPS}/officeDocument" Target="xl/workbook.xml"/></Relationships>""",
        "xl/workbook.xml": f"""<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="{OFFICE_RELATIONSHIPS}"><sheets><sheet name="Analysis" sheetId="1" r:id="rId1"/></sheets></workbook>""",
        "xl/_rels/workbook.xml.rels": f"""<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="{RELATIONSHIPS}"><Relationship Id="rId1" Type="{OFFICE_RELATIONSHIPS}/worksheet" Target="worksheets/sheet1.xml"/></Relationships>""",
        "xl/worksheets/sheet1.xml": f"""<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>{_xml(title)}</t></is></c></row><row r="2"><c r="A2" t="inlineStr"><is><t>Synthetic maintained document fixture</t></is></c></row></sheetData></worksheet>""",
    }
    return _zip(members)


def generate_pdf(title: str) -> bytes:
    document: Any = pydyf.PDF()
    font: Any = pydyf.Dictionary(
        {"Type": "/Font", "Subtype": "/Type1", "BaseFont": "/Helvetica", "Encoding": "/WinAnsiEncoding"}
    )
    document.add_object(font)
    text: Any = pydyf.Stream()
    text.begin_text()
    text.set_font_size("F1", 12)
    for content, position in ((title, 720), ("Synthetic maintained document fixture", 690)):
        text.set_text_matrix(1, 0, 0, 1, 72, position)
        encoded_content: Any = content.encode("cp1252")
        text.show_text(pydyf.String(encoded_content))
    text.end_text()
    document.add_object(text)
    document.add_page(
        pydyf.Dictionary(
            {
                "Type": "/Page",
                "Parent": document.pages.reference,
                "Contents": text.reference,
                "MediaBox": pydyf.Array([0, 0, 612, 792]),
                "Resources": pydyf.Dictionary({"Font": pydyf.Dictionary({"F1": font.reference})}),
            }
        )
    )
    document.info["Title"] = pydyf.String(title)
    output = io.BytesIO()
    document.write(output, version=b"1.7")
    return output.getvalue()


def validate_generated_document(document: GeneratedDocument, *, pdf_renderer: PageRenderer | None = None) -> None:
    if document.kind in {"pptx", "docx", "xlsx"}:
        report = validate_office(document.content, kind=document.kind)  # type: ignore[arg-type]
    elif document.kind == "pdf":
        report = validate_pdf(document.content, renderer=pdf_renderer)
    else:
        raise ValueError("unsupported generated document kind")
    if not report.passed:
        raise ValueError(f"generated {document.kind} fixture failed validation")


def _zip(members: dict[str, str]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in sorted(members):
            member = zipfile.ZipInfo(name, ZIP_TIMESTAMP)
            member.compress_type = zipfile.ZIP_DEFLATED
            member.external_attr = (0o100444) << 16
            archive.writestr(member, members[name].encode("utf-8"))
    return output.getvalue()


def _xml(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
