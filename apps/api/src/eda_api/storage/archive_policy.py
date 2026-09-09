from __future__ import annotations

import io
import re
import stat
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import BinaryIO

MAX_ARCHIVE_ENTRIES = 10_000
MAX_PATH_DEPTH = 20
MAX_MEMBER_BYTES = 100 * 1024 * 1024
MAX_TOTAL_BYTES = 500 * 1024 * 1024
MAX_COMPRESSION_RATIO = 100

OFFICE_ROOTS = {
    ".xlsx": "xl",
    ".xlsm": "xl",
    ".docx": "word",
    ".dotx": "word",
    ".pptx": "ppt",
    ".potx": "ppt",
}


class UnsafeArchive(ValueError):
    pass


@dataclass(frozen=True)
class OfficeArchiveInspection:
    has_vba_project: bool
    entry_count: int
    total_uncompressed_bytes: int


def inspect_office_archive(
    payload: bytes | BinaryIO,
    extension: str,
    *,
    max_ratio: int = MAX_COMPRESSION_RATIO,
) -> OfficeArchiveInspection:
    normalized_extension = extension.lower()
    expected_root = OFFICE_ROOTS.get(normalized_extension)
    if expected_root is None:
        raise UnsafeArchive("unsupported Office archive extension")
    if max_ratio < 1:
        raise ValueError("max_ratio must be positive")
    try:
        source = io.BytesIO(payload) if isinstance(payload, bytes) else payload
        with zipfile.ZipFile(source) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_ARCHIVE_ENTRIES:
                raise UnsafeArchive("too many archive entries")
            names: set[str] = set()
            total_uncompressed_bytes = 0
            for entry in entries:
                _validate_member_name(entry.filename)
                _validate_member_metadata(entry, max_ratio)
                total_uncompressed_bytes += entry.file_size
                if total_uncompressed_bytes > MAX_TOTAL_BYTES:
                    raise UnsafeArchive("archive exceeds total uncompressed size")
                names.add(entry.filename)
    except zipfile.BadZipFile as error:
        raise UnsafeArchive("invalid ZIP archive") from error

    if "[Content_Types].xml" not in names:
        raise UnsafeArchive("missing [Content_Types].xml")
    if not any(name.startswith(f"{expected_root}/") for name in names):
        raise UnsafeArchive("missing expected Office root")
    return OfficeArchiveInspection(
        has_vba_project=normalized_extension == ".xlsm" and "xl/vbaProject.bin" in names,
        entry_count=len(entries),
        total_uncompressed_bytes=total_uncompressed_bytes,
    )


def _validate_member_name(name: str) -> None:
    if not name or "\\" in name or re.match(r"^[A-Za-z]:", name):
        raise UnsafeArchive("unsafe archive member path")
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or len(path.parts) > MAX_PATH_DEPTH:
        raise UnsafeArchive("unsafe archive member path")


def _validate_member_metadata(entry: zipfile.ZipInfo, max_ratio: int) -> None:
    if entry.flag_bits & 0x1:
        raise UnsafeArchive("encrypted archive member")
    mode = entry.external_attr >> 16
    if stat.S_IFMT(mode) == stat.S_IFLNK:
        raise UnsafeArchive("symlink archive member")
    if entry.file_size > MAX_MEMBER_BYTES:
        raise UnsafeArchive("archive member exceeds uncompressed size")
    if entry.file_size > 0 and entry.compress_size == 0:
        raise UnsafeArchive("archive compression ratio exceeds limit")
    if entry.compress_size > 0 and entry.file_size / entry.compress_size > max_ratio:
        raise UnsafeArchive("archive compression ratio exceeds limit")
