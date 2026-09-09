from __future__ import annotations

from pathlib import Path

from .archive import ArchiveInspection, inspect_archive


def inspect_xlsx(path: Path) -> ArchiveInspection:
    inspection = inspect_archive(path.read_bytes())
    if "[Content_Types].xml" not in inspection.member_names or not any(
        name.startswith("xl/") for name in inspection.member_names
    ):
        raise ValueError("invalid OOXML workbook")
    return inspection
