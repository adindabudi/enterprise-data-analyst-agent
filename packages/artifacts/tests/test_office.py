from __future__ import annotations

import io
import zipfile
from pathlib import Path

from eda_artifacts.office import validate_office

CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Override PartName="{part}" ContentType="{content_type}"/>
</Types>"""
ROOT_RELS = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="officeDocument" Target="{target}"/>
</Relationships>"""


def package(kind: str, *, malformed_content_types: bool = False, empty_slides: bool = False) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        if kind == "docx":
            part = "/word/document.xml"
            target = "word/document.xml"
            content_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
            body = """<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p/></w:body></w:document>"""
            archive.writestr("word/document.xml", body)
        else:
            part = "/ppt/presentation.xml"
            target = "ppt/presentation.xml"
            content_type = "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"
            slide = "" if empty_slides else '<p:sldId id="256" r:id="rId1"/>'
            body = f"""<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><p:sldIdLst>{slide}</p:sldIdLst></p:presentation>"""
            archive.writestr("ppt/presentation.xml", body)
        declared_part = "/wrong.xml" if malformed_content_types else part
        archive.writestr(
            "[Content_Types].xml",
            CONTENT_TYPES.format(part=declared_part, content_type=content_type),
        )
        archive.writestr("_rels/.rels", ROOT_RELS.format(target=target))
    return output.getvalue()


def test_docx_structural_profile_and_official_validator_callback() -> None:
    calls: list[tuple[Path, Path | None]] = []

    def official(candidate: Path, original: Path | None) -> tuple[bool, tuple[str, ...]]:
        calls.append((candidate, original))
        return True, ()

    report = validate_office(
        package("docx"),
        kind="docx",
        original=package("docx"),
        official_validator=official,
    )

    assert report.passed is True
    assert report.member_count == 3
    assert report.official_validator_ran is True
    assert len(calls) == 1
    assert calls[0][1] is not None


def test_pptx_rejects_missing_content_type_or_empty_slide_list() -> None:
    content_type = validate_office(package("pptx", malformed_content_types=True), kind="pptx")
    empty = validate_office(package("pptx", empty_slides=True), kind="pptx")

    assert content_type.passed is False
    assert any("Content_Types" in failure for failure in content_type.failures)
    assert empty.passed is False
    assert any("sldIdLst" in failure for failure in empty.failures)


def test_office_rejects_traversal_and_non_zip_payloads() -> None:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("../escape.xml", "<x/>")

    assert validate_office(output.getvalue(), kind="docx").passed is False
    assert validate_office(b"not-office", kind="docx").passed is False


def nested_pptx(*, layout_target: str) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            CONTENT_TYPES.format(
                part="/ppt/presentation.xml",
                content_type="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml",
            ),
        )
        archive.writestr("_rels/.rels", ROOT_RELS.format(target="ppt/presentation.xml"))
        archive.writestr(
            "ppt/presentation.xml",
            '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'
            ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<p:sldIdLst><p:sldId id="256" r:id="rId1"/></p:sldIdLst></p:presentation>',
        )
        archive.writestr("ppt/slideMasters/slideMaster1.xml", "<x/>")
        archive.writestr("ppt/slideLayouts/slideLayout1.xml", "<x/>")
        archive.writestr(
            "ppt/slideLayouts/_rels/slideLayout1.xml.rels",
            ROOT_RELS.format(target=layout_target),
        )
    return output.getvalue()


def test_relationship_targets_resolve_parent_segments_without_escaping() -> None:
    # A real pptx points a slide layout at ../slideMasters/slideMaster1.xml.
    resolved = validate_office(nested_pptx(layout_target="../slideMasters/slideMaster1.xml"), kind="pptx")
    escaping = validate_office(nested_pptx(layout_target="../../../etc/passwd"), kind="pptx")

    assert resolved.failures == ()
    assert escaping.passed is False
    assert any("escapes the package" in failure for failure in escaping.failures)
