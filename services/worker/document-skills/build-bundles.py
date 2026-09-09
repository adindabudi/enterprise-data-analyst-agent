from __future__ import annotations

import argparse
import hashlib
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path

SKILL_NAMES = ("docx", "pdf", "pptx", "xlsx")
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


@dataclass(frozen=True)
class SkillBundle:
    path: Path
    sha256: str


def build_bundles(skill_root: Path, output_dir: Path) -> tuple[SkillBundle, ...]:
    output_dir.mkdir(parents=True, exist_ok=True)
    bundles: list[SkillBundle] = []
    for name in SKILL_NAMES:
        skill_directory = skill_root / name
        if not skill_directory.is_dir() or skill_directory.is_symlink():
            raise ValueError(f"document skill directory is missing or unsafe: {name}")
        bundle_path = output_dir / f"{name}.zip"
        with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for path in included_files(skill_directory):
                relative = path.relative_to(skill_directory).as_posix()
                member = zipfile.ZipInfo(relative, date_time=ZIP_TIMESTAMP)
                member.compress_type = zipfile.ZIP_DEFLATED
                member.external_attr = (stat.S_IFREG | 0o444) << 16
                archive.writestr(member, path.read_bytes())
        bundles.append(SkillBundle(path=bundle_path, sha256=hashlib.sha256(bundle_path.read_bytes()).hexdigest()))
    return tuple(bundles)


def included_files(skill_directory: Path) -> tuple[Path, ...]:
    files: list[Path] = []
    for path in skill_directory.rglob("*"):
        relative_parts = path.relative_to(skill_directory).parts
        if any(part.startswith(".") for part in relative_parts):
            continue
        if path.is_symlink() or not path.is_file():
            if path.is_symlink():
                raise ValueError("document skill bundle contains a symlink")
            continue
        files.append(path)
    return tuple(sorted(files, key=lambda path: path.relative_to(skill_directory).as_posix()))


def zip_member_names(bundle_path: Path) -> tuple[str, ...]:
    with zipfile.ZipFile(bundle_path) as archive:
        return tuple(archive.namelist())


def main() -> None:
    parser = argparse.ArgumentParser(description="Package acquired document skills into deterministic ZIP bundles.")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    for bundle in build_bundles(arguments.source, arguments.output):
        print(f"{bundle.path.name} {bundle.sha256}")


if __name__ == "__main__":
    main()
