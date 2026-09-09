from __future__ import annotations

import hashlib
import io
import os
import secrets
import stat
from collections.abc import Iterable
from pathlib import Path
from typing import BinaryIO, Literal

from .contracts import FileRecord
from .settings import IMPORTS, MAX_IMPORT_BYTES, MAX_OUTPUT_BYTES, MAX_OUTPUT_FILES, MAX_SOURCE_BYTES, OUTPUTS, SOURCES

FileCategory = Literal["input", "source", "output", "stdio", "validation"]


class UnsafeFile(ValueError):
    pass


class FileLimitExceeded(ValueError):
    pass


def sanitize_display_name(name: str) -> str:
    value = name.replace("\x00", "").replace("\\", "/").split("/")[-1]
    return value or "unnamed"


class FileIndex:
    def __init__(self) -> None:
        self.max_import_bytes = MAX_IMPORT_BYTES
        self._records: dict[str, FileRecord] = {}
        self._paths: dict[str, Path] = {}
        for category in ("input", "source", "output", "stdio", "validation"):
            self.category_path(category).mkdir(parents=True, exist_ok=True)

    def category_path(self, category: FileCategory) -> Path:
        roots = {"input": IMPORTS, "source": SOURCES, "output": OUTPUTS, "stdio": OUTPUTS, "validation": OUTPUTS}
        return roots[category]

    def import_bytes(self, category: FileCategory, display_name: str, body: bytes) -> FileRecord:
        return self.import_stream(category, display_name, io.BytesIO(body))

    def import_stream(self, category: FileCategory, display_name: str, stream: BinaryIO) -> FileRecord:
        file_id = f"file_{secrets.token_urlsafe(18)}"
        suffix = _safe_suffix(display_name)
        path = self.category_path(category) / f"{file_id}{suffix}"
        descriptor = -1
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            digest = hashlib.sha256()
            size_bytes = 0
            with os.fdopen(descriptor, "wb", closefd=True) as handle:
                descriptor = -1
                while chunk := stream.read(1024 * 1024):
                    size_bytes += len(chunk)
                    if size_bytes > self._limit(category):
                        raise FileLimitExceeded("file exceeds category limit")
                    digest.update(chunk)
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            record = FileRecord(
                file_id=file_id,
                category=category,
                display_name=sanitize_display_name(display_name),
                size_bytes=size_bytes,
                sha256=digest.hexdigest(),
            )
            self._records[file_id] = record
            self._paths[file_id] = path
            return record
        except Exception:
            path.unlink(missing_ok=True)
            raise
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    def path_for(self, file_id: str) -> Path:
        path = self._paths.get(file_id)
        if path is None:
            raise UnsafeFile("unknown file ID")
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_nlink != 1:
            raise UnsafeFile("file is not a regular indexed file")
        if path.parent not in {IMPORTS, SOURCES, OUTPUTS}:
            raise UnsafeFile("file is outside sandbox categories")
        return path

    def record_for(self, file_id: str) -> FileRecord:
        record = self._records.get(file_id)
        if record is None:
            raise UnsafeFile("unknown file ID")
        self.path_for(file_id)
        return record

    def records(self, categories: Iterable[FileCategory]) -> tuple[FileRecord, ...]:
        allowed = set(categories)
        return tuple(record for record in self._records.values() if record.category in allowed)

    def clear(self) -> None:
        for path in self._paths.values():
            path.unlink(missing_ok=True)
        self._records.clear()
        self._paths.clear()

    def index_output(self, path: Path) -> FileRecord:
        for file_id, indexed_path in self._paths.items():
            if path == indexed_path:
                return self._records[file_id]
        if path.parent != OUTPUTS:
            raise UnsafeFile("output is outside the output directory")
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_nlink != 1:
            raise UnsafeFile("output is unsafe")
        if info.st_size > MAX_OUTPUT_BYTES:
            raise FileLimitExceeded("output file exceeds limit")
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        file_id = f"file_{secrets.token_urlsafe(18)}"
        target = OUTPUTS / f"{file_id}{_safe_suffix(path.name)}"
        os.replace(path, target)
        target.chmod(0o600)
        record = FileRecord(
            file_id=file_id,
            category="output",
            display_name=sanitize_display_name(path.name),
            size_bytes=info.st_size,
            sha256=digest.hexdigest(),
        )
        self._records[file_id] = record
        self._paths[file_id] = target
        return record

    def scan_outputs(self) -> list[FileRecord]:
        indexed_paths = set(self._paths.values())
        paths = [path for path in OUTPUTS.iterdir() if path not in indexed_paths]
        if len(paths) > MAX_OUTPUT_FILES:
            raise FileLimitExceeded("too many output files")
        total = 0
        records: list[FileRecord] = []
        for path in paths:
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_nlink != 1:
                raise UnsafeFile("output is unsafe")
            total += info.st_size
            if total > MAX_OUTPUT_BYTES:
                raise FileLimitExceeded("output bytes exceed limit")
            records.append(self.index_output(path))
        return records

    def _limit(self, category: FileCategory) -> int:
        if category == "input":
            return self.max_import_bytes
        if category == "source":
            return MAX_SOURCE_BYTES
        return MAX_OUTPUT_BYTES


def _safe_suffix(display_name: str) -> str:
    suffix = Path(sanitize_display_name(display_name)).suffix.lower()
    return suffix if suffix and suffix.isalnum() is False and len(suffix) <= 16 else ""
