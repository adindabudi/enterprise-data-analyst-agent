from __future__ import annotations

import io
import stat
import zipfile

import pytest
from eda_artifacts.archive import UnsafeArchive, inspect_archive


def make_zip(entries: list[tuple[str, bytes]]) -> bytes:
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, body in entries:
            archive.writestr(name, body)
    return data.getvalue()


@pytest.mark.parametrize("name", ["../escape", "/absolute", "C:/device", "a/../../escape", "dir\\escape"])
def test_archive_rejects_unsafe_member_name(name: str) -> None:
    with pytest.raises(UnsafeArchive):
        inspect_archive(make_zip([(name, b"x")]))


def test_archive_rejects_excessive_compression_ratio() -> None:
    with pytest.raises(UnsafeArchive, match="compression ratio"):
        inspect_archive(make_zip([("large.txt", b"0" * 2_000_000)]), max_ratio=20)


def test_archive_rejects_symlink_member() -> None:
    data = io.BytesIO()
    info = zipfile.ZipInfo("link")
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr(info, b"target")

    with pytest.raises(UnsafeArchive, match="symlink"):
        inspect_archive(data.getvalue())
