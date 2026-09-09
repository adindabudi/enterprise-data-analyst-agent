from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

from eda_artifacts.html import validate_html, validate_web_artifact_html
from eda_artifacts.images import validate_png, validate_svg
from eda_artifacts.manifests import validate_manifest
from eda_artifacts.mermaid import validate_mermaid
from eda_artifacts.office import acquired_office_validator, validate_office
from eda_artifacts.pdf import validate_pdf
from eda_artifacts.xlsx import inspect_xlsx

from .contracts import ValidationProfile, ValidationRequest, ValidationResult, ValidationStatus
from .files import FileIndex

MAX_TEXT_VALIDATION_BYTES = 10 * 1024 * 1024


def validate_path(profile: ValidationProfile, path: Path) -> None:
    _run_validator(profile, path.name, path)


def validate_indexed_file(index: FileIndex, request: ValidationRequest) -> ValidationResult:
    record = index.record_for(request.file_id)
    path = index.path_for(request.file_id)
    try:
        _run_validator(request.profile, record.display_name, path)
        status = ValidationStatus.PASSED
        errors: tuple[str, ...] = ()
    except (OSError, ValueError, json.JSONDecodeError):
        status = ValidationStatus.FAILED
        errors = ("validation_failed",)

    report_payload = json.dumps(
        {
            "schemaVersion": "1.0",
            "fileId": record.file_id,
            "fileSha256": record.sha256,
            "profile": request.profile.value,
            "status": status.value,
            "errors": errors,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    report = index.import_bytes("validation", f"{record.file_id}.validation.json", report_payload)
    validation_id = sha256(f"{record.file_id}|{record.sha256}|{request.profile.value}".encode()).hexdigest()[:16]
    return ValidationResult(
        validation_id=f"validation_{validation_id}",
        file_id=record.file_id,
        profile=request.profile,
        status=status,
        report=report,
    )


def _run_validator(profile: ValidationProfile, display_name: str, path: Path) -> None:
    suffix = Path(display_name).suffix.lower()
    if profile is ValidationProfile.CORE_XLSX:
        if suffix not in {".xlsx", ".xlsm"}:
            raise ValueError("workbook profile requires OOXML")
        inspect_xlsx(path)
        return
    if profile is ValidationProfile.CORE_HTML:
        if suffix not in {".html", ".htm"}:
            raise ValueError("HTML profile requires HTML")
        validate_html(_read_text(path))
        return
    if profile is ValidationProfile.WEB_ARTIFACT_HTML:
        if suffix not in {".html", ".htm"}:
            raise ValueError("web artifact profile requires HTML")
        validate_web_artifact_html(_read_text(path))
        return
    if profile is ValidationProfile.CORE_CHART:
        _validate_image(suffix, path)
        return
    if profile is ValidationProfile.CORE_MERMAID:
        if suffix == ".mmd":
            validate_mermaid(_read_text(path))
        else:
            _validate_image(suffix, path)
        return
    if profile is ValidationProfile.PROVENANCE:
        payload: object = json.loads(_read_text(path))
        if not isinstance(payload, dict):
            raise ValueError("manifest must be an object")
        validate_manifest(cast(dict[str, Any], payload))
        return
    if profile is ValidationProfile.DOCUMENT_PPTX:
        if suffix not in {".pptx", ".potx"}:
            raise ValueError("PPTX profile requires a presentation package")
        validator_path = Path("/opt/document-skills/pptx/scripts/office/validate.py")
        report = validate_office(
            path.read_bytes(),
            kind="pptx",
            official_validator=acquired_office_validator(validator_path) if validator_path.is_file() else None,
        )
        if not report.passed:
            raise ValueError("PPTX validation failed")
        return
    if profile is ValidationProfile.DOCUMENT_DOCX:
        if suffix not in {".docx", ".dotx"}:
            raise ValueError("DOCX profile requires a word-processing package")
        validator_path = Path("/opt/document-skills/docx/scripts/office/validate.py")
        report = validate_office(
            path.read_bytes(),
            kind="docx",
            official_validator=acquired_office_validator(validator_path) if validator_path.is_file() else None,
        )
        if not report.passed:
            raise ValueError("DOCX validation failed")
        return
    if profile is ValidationProfile.DOCUMENT_XLSX:
        if suffix not in {".xlsx", ".xltx"}:
            raise ValueError("XLSX profile requires a spreadsheet package")
        # The acquired skill validator exits 0 for xlsx without inspecting anything, so it is
        # deliberately not consulted here; the package and workbook checks below do the work.
        if not validate_office(path.read_bytes(), kind="xlsx").passed:
            raise ValueError("XLSX validation failed")
        inspect_xlsx(path)
        return
    if profile is ValidationProfile.DOCUMENT_PDF:
        if suffix != ".pdf":
            raise ValueError("PDF profile requires PDF")
        if not validate_pdf(path.read_bytes()).passed:
            raise ValueError("PDF validation failed")
        return
    raise ValueError("unsupported validation profile")


def _validate_image(suffix: str, path: Path) -> None:
    if suffix == ".png":
        validate_png(path.read_bytes())
        return
    if suffix == ".svg":
        validate_svg(_read_text(path))
        return
    raise ValueError("image profile requires PNG or SVG")


def _read_text(path: Path) -> str:
    if path.stat().st_size > MAX_TEXT_VALIDATION_BYTES:
        raise ValueError("text artifact exceeds validation limit")
    return path.read_text(encoding="utf-8")
