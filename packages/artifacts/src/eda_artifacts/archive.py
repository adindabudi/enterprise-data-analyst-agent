from __future__ import annotations

import io
import re
import stat
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath

MAX_ENTRIES = 10000
MAX_PATH_DEPTH = 20
MAX_MEMBER_BYTES = 100 * 1024 * 1024
MAX_TOTAL_BYTES = 500 * 1024 * 1024
MAX_RATIO = 100


class UnsafeArchive(ValueError):
    pass


@dataclass(frozen=True)
class ArchiveInspection:
    entry_count: int
    total_uncompressed_bytes: int
    member_names: tuple[str, ...]


def inspect_archive(payload: bytes, *, max_ratio: int = MAX_RATIO) -> ArchiveInspection:
    if max_ratio < 1:
        raise ValueError("max_ratio must be positive")
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_ENTRIES:
                raise UnsafeArchive("too many archive entries")
            total = 0
            names: list[str] = []
            for entry in entries:
                _validate_name(entry.filename)
                _validate_info(entry, max_ratio)
                total += entry.file_size
                if total > MAX_TOTAL_BYTES:
                    raise UnsafeArchive("archive exceeds total uncompressed size")
                names.append(entry.filename)
    except zipfile.BadZipFile as error:
        raise UnsafeArchive("invalid ZIP archive") from error
    return ArchiveInspection(entry_count=len(names), total_uncompressed_bytes=total, member_names=tuple(names))


def _validate_name(name: str) -> None:
    if not name or "\\" in name or re.match(r"^[A-Za-z]:", name):
        raise UnsafeArchive("unsafe archive member path")
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or len(path.parts) > MAX_PATH_DEPTH:
        raise UnsafeArchive("unsafe archive member path")


def _validate_info(entry: zipfile.ZipInfo, max_ratio: int) -> None:
    if entry.flag_bits & 0x1:
        raise UnsafeArchive("encrypted archive member")
    if stat.S_IFMT(entry.external_attr >> 16) == stat.S_IFLNK:
        raise UnsafeArchive("symlink archive member")
    if entry.file_size > MAX_MEMBER_BYTES:
        raise UnsafeArchive("archive member exceeds uncompressed size")
    if entry.file_size > 0 and entry.compress_size == 0:
        raise UnsafeArchive("archive compression ratio exceeds limit")
    if entry.compress_size > 0 and entry.file_size / entry.compress_size > max_ratio:
        raise UnsafeArchive("archive compression ratio exceeds limit")
