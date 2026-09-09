from __future__ import annotations

import hashlib
import json
import shutil
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from document_skills_contract import SkillLockEntry, SkillPack, SkillsLock, load_skill_pack


class UnsafeSkillContentError(ValueError):
    pass


class SkillHashMismatchError(ValueError):
    pass


@dataclass(frozen=True)
class AcquisitionEntry:
    name: str
    allowed_paths: tuple[str, ...]
    skill_md_sha256: str
    license_sha256: str

    @classmethod
    def from_lock_entry(cls, entry: SkillLockEntry) -> AcquisitionEntry:
        return cls(
            name=entry.name,
            allowed_paths=entry.allowed_paths,
            skill_md_sha256=entry.skill_md_sha256,
            license_sha256=entry.license_sha256,
        )


def acquire_skill(
    source: Path,
    entry: AcquisitionEntry,
    destination: Path,
    *,
    metadata: dict[str, str] | None = None,
    readonly: bool = True,
) -> None:
    if destination.exists():
        raise UnsafeSkillContentError("document skill destination already exists")
    if not source.is_dir() or source.is_symlink():
        raise UnsafeSkillContentError("document skill source must be a real directory")
    verify_skill_tree(source, entry)
    shutil.copytree(source, destination, symlinks=True, copy_function=shutil.copyfile)
    if metadata is not None:
        (destination / ".acquired.json").write_text(json.dumps(metadata, sort_keys=True, separators=(",", ":")) + "\n")
    if readonly:
        make_readonly(destination)


def verify_skill_tree(source: Path, entry: AcquisitionEntry, *, allow_metadata: bool = False) -> None:
    expected_paths = {"SKILL.md", "LICENSE.txt"}
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source).as_posix()
        if path.is_symlink():
            raise UnsafeSkillContentError("document skill contains a symlink")
        if path.is_dir():
            continue
        if allow_metadata and relative == ".acquired.json":
            continue
        if not allowed_path(relative, entry.allowed_paths):
            raise UnsafeSkillContentError("document skill path is not in allowlist")
        if path.suffix not in {".gz", ".md", ".py", ".sh", ".txt", ".xml", ".xsd"}:
            raise UnsafeSkillContentError("document skill contains an unexpected file type")
        expected_paths.discard(relative)
    if expected_paths:
        raise UnsafeSkillContentError("document skill is missing required content")
    verify_hash(source / "SKILL.md", entry.skill_md_sha256, "SKILL.md")
    verify_hash(source / "LICENSE.txt", entry.license_sha256, "LICENSE.txt")


def acquire_pack(lock_path: Path, destination_root: Path, pack: SkillPack) -> SkillsLock:
    lock, selected_entries = load_skill_pack(lock_path, pack)
    tracked_helpers = {".gitkeep", "build-bundles.py"}
    if destination_root.exists() and any(path.name not in tracked_helpers for path in destination_root.iterdir()):
        raise UnsafeSkillContentError("document skill destination must be empty")
    destination_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination_root.parent) as temporary_directory:
        repository = Path(temporary_directory) / "skills"
        staging_root = Path(temporary_directory) / "document-skills"
        staging_root.mkdir()
        run_git("clone", "--filter=blob:none", "--no-checkout", lock.source, str(repository))
        run_git("-C", str(repository), "fetch", "--depth=1", "origin", lock.commit)
        run_git("-C", str(repository), "sparse-checkout", "init", "--cone")
        run_git("-C", str(repository), "sparse-checkout", "set", *(entry.path for entry in selected_entries))
        run_git("-C", str(repository), "checkout", "--detach", "FETCH_HEAD")
        checked_out = run_git("-C", str(repository), "rev-parse", "HEAD").strip()
        if checked_out != lock.commit:
            raise UnsafeSkillContentError("document skill checkout commit does not match lock")
        for lock_entry in selected_entries:
            metadata = {
                "commit": lock.commit,
                "licenseSha256": lock_entry.license_sha256,
                "skillMdSha256": lock_entry.skill_md_sha256,
                "verifiedAt": datetime.now(UTC).isoformat(),
            }
            acquire_skill(
                repository / lock_entry.path,
                AcquisitionEntry.from_lock_entry(lock_entry),
                staging_root / lock_entry.name,
                metadata=metadata,
                readonly=False,
            )
        for lock_entry in selected_entries:
            destination = destination_root / lock_entry.name
            shutil.move(str(staging_root / lock_entry.name), destination)
            make_readonly(destination)
    return lock


def acquire_all(lock_path: Path, destination_root: Path) -> SkillsLock:
    return acquire_pack(lock_path, destination_root, "all")


def validate_pack(lock_path: Path, destination_root: Path, pack: SkillPack) -> SkillsLock:
    lock, selected_entries = load_skill_pack(lock_path, pack)
    for lock_entry in selected_entries:
        destination = destination_root / lock_entry.name
        verify_skill_tree(destination, AcquisitionEntry.from_lock_entry(lock_entry), allow_metadata=True)
        for path in destination.rglob("*"):
            if path.is_file() and path.stat().st_mode & stat.S_IWUSR:
                raise UnsafeSkillContentError("acquired document skill contains a writable file")
    return lock


def validate_acquired(lock_path: Path, destination_root: Path) -> SkillsLock:
    return validate_pack(lock_path, destination_root, "all")


def allowed_path(relative: str, allowed_paths: tuple[str, ...]) -> bool:
    return any(
        relative == allowed or (allowed.endswith("/") and relative.startswith(allowed)) for allowed in allowed_paths
    )


def verify_hash(path: Path, expected: str, label: str) -> None:
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected:
        raise SkillHashMismatchError(f"document skill {label} hash does not match lock")


def make_readonly(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_dir():
            path.chmod(0o555)
        else:
            path.chmod(0o444)
    root.chmod(0o555)


def run_git(*arguments: str) -> str:
    git_executable = shutil.which("git")
    if git_executable is None:
        raise UnsafeSkillContentError("git is required for document skill acquisition")
    result = subprocess.run(  # noqa: S603 - fixed executable and subcommand; shell is never used.
        [git_executable, *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout
