from __future__ import annotations

import io
import subprocess
import tempfile
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from defusedxml import ElementTree

type OfficeKind = Literal["docx", "pptx", "xlsx"]
type OfficialValidator = Callable[[Path, Path | None], tuple[bool, tuple[str, ...]]]

MAX_OFFICE_BYTES = 100 * 1024 * 1024
MAX_MEMBERS = 10_000
MAX_EXPANDED_BYTES = 500 * 1024 * 1024


@dataclass(frozen=True)
class OfficeValidationReport:
    passed: bool
    failures: tuple[str, ...]
    member_count: int = 0
    official_validator_ran: bool = False


def validate_office(
    payload: bytes,
    *,
    kind: OfficeKind,
    original: bytes | None = None,
    official_validator: OfficialValidator | None = None,
) -> OfficeValidationReport:
    failures: list[str] = []
    if not payload or len(payload) > MAX_OFFICE_BYTES:
        return OfficeValidationReport(False, ("Office artifact is empty or exceeds 100 MiB.",))
    if not payload.startswith(b"PK"):
        return OfficeValidationReport(False, ("Office artifact is not an OOXML ZIP package.",))
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            members = archive.infolist()
            if not members or len(members) > MAX_MEMBERS:
                raise ValueError("OOXML member count is invalid")
            expanded = 0
            names: set[str] = set()
            for member in members:
                normalized = Path(member.filename)
                if member.is_dir():
                    continue
                if member.filename.startswith("/") or ".." in normalized.parts or member.filename in names:
                    raise ValueError("OOXML package contains an unsafe or duplicate member")
                if member.external_attr >> 16 & 0o170000 == 0o120000:
                    raise ValueError("OOXML package contains a symlink")
                expanded += member.file_size
                if expanded > MAX_EXPANDED_BYTES:
                    raise ValueError("OOXML package exceeds expanded-size limit")
                names.add(member.filename)
            required = _required_members(kind)
            missing = required - names
            if missing:
                failures.append(f"OOXML package is missing: {', '.join(sorted(missing))}.")
            for xml_name in _xml_members(names):
                try:
                    ElementTree.fromstring(archive.read(xml_name))
                except Exception:
                    failures.append(f"OOXML XML is malformed: {xml_name}.")
            if "[Content_Types].xml" in names:
                _validate_content_types(archive.read("[Content_Types].xml"), kind, failures)
            _validate_relationship_targets(archive, names, failures)
            if kind == "pptx" and "ppt/presentation.xml" in names:
                _validate_presentation(archive.read("ppt/presentation.xml"), failures)
            if kind == "docx" and "word/document.xml" in names:
                _validate_document(archive.read("word/document.xml"), failures)
            member_count = len(names)
    except (OSError, ValueError, zipfile.BadZipFile):
        return OfficeValidationReport(False, ("Office artifact ZIP structure is invalid.",))

    official_ran = False
    validator = official_validator
    if validator is not None and not failures:
        official_ran = True
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            suffix = f".{kind}"
            candidate = root / f"candidate{suffix}"
            candidate.write_bytes(payload)
            original_path: Path | None = None
            if original is not None:
                original_path = root / f"original{suffix}"
                original_path.write_bytes(original)
            passed, validator_failures = validator(candidate, original_path)
            if not passed:
                failures.extend(validator_failures or ("Official OOXML validator rejected the artifact.",))
    return OfficeValidationReport(not failures, tuple(failures), member_count, official_ran)


def acquired_office_validator(script_path: Path) -> OfficialValidator:
    resolved = script_path.resolve()

    def run(candidate: Path, original: Path | None) -> tuple[bool, tuple[str, ...]]:
        command = ["python", str(resolved), str(candidate)]
        if original is not None:
            command.extend(["--original", str(original)])
        result = subprocess.run(  # noqa: S603 - fixed acquired script and temporary bounded files.
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 0:
            return True, ()
        detail = (result.stderr or result.stdout).strip()[:2_000]
        return False, (detail or "Official OOXML validator rejected the artifact.",)

    return run


MAIN_PART: dict[str, str] = {
    "docx": "word/document.xml",
    "pptx": "ppt/presentation.xml",
    "xlsx": "xl/workbook.xml",
}


def _required_members(kind: OfficeKind) -> set[str]:
    return {"[Content_Types].xml", "_rels/.rels", MAIN_PART[kind]}


def _xml_members(names: set[str]) -> list[str]:
    return sorted(name for name in names if name.endswith((".xml", ".rels")))


def _validate_content_types(payload: bytes, kind: OfficeKind, failures: list[str]) -> None:
    root = ElementTree.fromstring(payload)
    overrides = {
        node.attrib.get("PartName"): node.attrib.get("ContentType") for node in root if node.tag.endswith("Override")
    }
    required_part = f"/{MAIN_PART[kind]}"
    if required_part not in overrides:
        failures.append(f"[Content_Types].xml does not declare {required_part}.")


def _validate_relationship_targets(
    archive: zipfile.ZipFile,
    names: set[str],
    failures: list[str],
) -> None:
    for rel_name in sorted(name for name in names if name.endswith(".rels")):
        root = ElementTree.fromstring(archive.read(rel_name))
        source = _relationship_source(rel_name)
        for relationship in root:
            if relationship.attrib.get("TargetMode") == "External":
                continue
            target = relationship.attrib.get("Target")
            if not target:
                failures.append(f"Relationship target is missing in {rel_name}.")
                continue
            candidate = _resolve_relationship_target(source.parent, target)
            if candidate is None:
                failures.append(f"Relationship target escapes the package in {rel_name}.")
                continue
            if candidate and candidate not in names:
                failures.append(f"Relationship target is absent: {candidate}.")


def _resolve_relationship_target(base: Path, target: str) -> str | None:
    """Resolve an OPC relationship target, or None when it escapes the package root."""
    parts: list[str] = []
    for part in (base / target).as_posix().lstrip("/").split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            if not parts:
                return None
            parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)


def _relationship_source(rel_name: str) -> Path:
    path = Path(rel_name)
    if rel_name == "_rels/.rels":
        return Path("/")
    parts = list(path.parts)
    rels_index = parts.index("_rels")
    filename = parts[-1].removesuffix(".rels")
    return Path(*parts[:rels_index], filename)


def _validate_presentation(payload: bytes, failures: list[str]) -> None:
    root = ElementTree.fromstring(payload)
    slide_lists = [node for node in root.iter() if node.tag.endswith("sldIdLst")]
    if len(slide_lists) != 1 or not list(slide_lists[0]):
        failures.append("presentation.xml has no populated sldIdLst.")


def _validate_document(payload: bytes, failures: list[str]) -> None:
    root = ElementTree.fromstring(payload)
    bodies = [node for node in root.iter() if node.tag.endswith("body")]
    if len(bodies) != 1:
        failures.append("document.xml has no single document body.")
