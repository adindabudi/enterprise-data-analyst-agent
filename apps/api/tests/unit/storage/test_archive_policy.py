from __future__ import annotations

import io
import zipfile
from collections.abc import Iterable

import pytest
from eda_api.storage.archive_policy import UnsafeArchive, inspect_office_archive


def zip_factory(entries: Iterable[tuple[str, bytes]]) -> bytes:
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
        zipped.writestr("[Content_Types].xml", b"<Types />")
        zipped.writestr("xl/workbook.xml", b"<workbook />")
        for name, contents in entries:
            zipped.writestr(name, contents)
    return archive.getvalue()


@pytest.mark.parametrize("entry", ["../escape", "/absolute", "a/../../escape", "C:/device", "folder\\escape"])
def test_archive_rejects_unsafe_names(entry: str) -> None:
    with pytest.raises(UnsafeArchive):
        inspect_office_archive(zip_factory([(entry, b"x")]), ".xlsx")


def test_archive_rejects_expansion_ratio() -> None:
    with pytest.raises(UnsafeArchive, match="compression ratio"):
        inspect_office_archive(zip_factory([("xl/sharedStrings.xml", b"0" * 2_000_000)]), ".xlsx", max_ratio=20)


def test_archive_requires_expected_office_root() -> None:
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("[Content_Types].xml", b"<Types />")
        zipped.writestr("word/document.xml", b"<document />")

    with pytest.raises(UnsafeArchive, match="expected Office root"):
        inspect_office_archive(archive.getvalue(), ".xlsx")


def test_xlsm_reports_macro_presence_without_reading_macro_payload() -> None:
    inspection = inspect_office_archive(zip_factory([("xl/vbaProject.bin", b"macro")]), ".xlsm")

    assert inspection.has_vba_project is True
