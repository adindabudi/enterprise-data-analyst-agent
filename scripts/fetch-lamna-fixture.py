from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path
from typing import Annotated
from urllib.parse import urlparse

import httpx
from pydantic import AnyUrl, BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = ROOT / "tests" / "fixtures" / "fabric" / "lamna-healthcare" / "fixture-lock.json"
MAX_COMPRESSED_BYTES = 10 * 1024 * 1024
MAX_EXPANDED_BYTES = 20 * 1024 * 1024


class FixtureLock(BaseModel):
    schema_version: int = Field(alias="schemaVersion", ge=1)
    name: str
    license: str
    upstream_repository: AnyUrl = Field(alias="upstreamRepository")
    upstream_commit: Annotated[str, Field(pattern=r"^[a-f0-9]{40}$")] = Field(alias="upstreamCommit")
    lab_url: AnyUrl = Field(alias="labUrl")
    archive_url: AnyUrl = Field(alias="archiveUrl")
    sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    files: tuple[str, ...]


def validate_archive_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc or not parsed.path.endswith(".zip"):
        raise ValueError("fixture archive must use an HTTPS ZIP URL")
    if parsed.params or parsed.query or parsed.fragment:
        raise ValueError("fixture archive URL must not contain parameters, query, or fragment")


def download_archive(lock: FixtureLock) -> bytes:
    archive_url = str(lock.archive_url)
    validate_archive_url(archive_url)
    with httpx.Client(follow_redirects=False, timeout=30) as client:
        with client.stream("GET", archive_url) as response:
            response.raise_for_status()
            chunks: list[bytes] = []
            size = 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > MAX_COMPRESSED_BYTES:
                    raise ValueError("fixture archive exceeds compressed size limit")
                chunks.append(chunk)
    payload = b"".join(chunks)
    if hashlib.sha256(payload).hexdigest() != lock.sha256:
        raise ValueError("fixture archive SHA-256 does not match the pinned lock")
    return payload


def extract_archive(payload: bytes, lock: FixtureLock, output: Path) -> None:
    allowed_files = set(lock.files)
    with zipfile.ZipFile(__import__("io").BytesIO(payload)) as archive:
        members = archive.infolist()
        names = {member.filename for member in members}
        if names != allowed_files:
            raise ValueError("fixture archive contains files outside the allowlist")
        if sum(member.file_size for member in members) > MAX_EXPANDED_BYTES:
            raise ValueError("fixture archive exceeds expanded size limit")
        if any(stat.S_ISLNK(member.external_attr >> 16) for member in members):
            raise ValueError("fixture archive must not contain symlinks")
        if any(Path(member.filename).name != member.filename for member in members):
            raise ValueError("fixture archive paths must be root files")
        output.mkdir(mode=0o700, parents=True, exist_ok=False)
        for member in members:
            target = output / member.filename
            with archive.open(member) as source, target.open("xb") as destination:
                shutil.copyfileobj(source, destination)
            target.chmod(0o600)


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch the pinned synthetic Lamna fixture.")
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    lock = FixtureLock.model_validate_json(LOCK_PATH.read_text())
    payload = download_archive(lock)
    arguments.output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=arguments.output.parent) as temporary_directory:
        temporary_output = Path(temporary_directory) / lock.name
        extract_archive(payload, lock, temporary_output)
        if arguments.output.exists():
            raise ValueError("fixture output already exists")
        shutil.move(str(temporary_output), arguments.output)
    print(json.dumps({"fixture": lock.name, "sha256": lock.sha256, "output": str(arguments.output)}))


if __name__ == "__main__":
    main()
