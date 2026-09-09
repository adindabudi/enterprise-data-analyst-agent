from __future__ import annotations

import hashlib
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "scripts" / "document_skills_acquisition.py"


def load_module() -> Any:
    scripts_directory = str(ROOT / "scripts")
    if scripts_directory not in sys.path:
        sys.path.insert(0, scripts_directory)
    specification = spec_from_file_location("document_skills_acquisition", MODULE_PATH)
    assert specification is not None
    assert specification.loader is not None
    module = module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


def make_skill_tree(tmp_path: Path, module: Any) -> tuple[Path, object]:
    source = tmp_path / "source"
    (source / "scripts").mkdir(parents=True)
    skill = "# Fixture skill\n"
    license_text = "fixture license\n"
    (source / "SKILL.md").write_text(skill)
    (source / "LICENSE.txt").write_text(license_text)
    (source / "scripts" / "validate.py").write_text("print('valid')\n")
    entry = module.AcquisitionEntry(
        name="docx",
        allowed_paths=("SKILL.md", "LICENSE.txt", "scripts/"),
        skill_md_sha256=hashlib.sha256(skill.encode()).hexdigest(),
        license_sha256=hashlib.sha256(license_text.encode()).hexdigest(),
    )
    return source, entry


def test_rejects_symlink_entries(tmp_path: Path) -> None:
    module = load_module()
    source, entry = make_skill_tree(tmp_path, module)
    (source / "scripts" / "link.py").symlink_to("validate.py")

    with pytest.raises(module_error(module, "UnsafeSkillContentError"), match="symlink"):
        module.acquire_skill(source, entry, tmp_path / "destination")


def test_rejects_path_outside_allowlist(tmp_path: Path) -> None:
    module = load_module()
    source, entry = make_skill_tree(tmp_path, module)
    (source / "unexpected.md").write_text("not allowed\n")

    with pytest.raises(module_error(module, "UnsafeSkillContentError"), match="allowlist"):
        module.acquire_skill(source, entry, tmp_path / "destination")


def test_rejects_hash_mismatch(tmp_path: Path) -> None:
    module = load_module()
    source, entry = make_skill_tree(tmp_path, module)
    (source / "SKILL.md").write_text("changed\n")

    with pytest.raises(module_error(module, "SkillHashMismatchError")):
        module.acquire_skill(source, entry, tmp_path / "destination")


def test_rejects_unexpected_binary_type(tmp_path: Path) -> None:
    module = load_module()
    source, entry = make_skill_tree(tmp_path, module)
    (source / "scripts" / "payload.bin").write_bytes(b"\x00\x01")

    with pytest.raises(module_error(module, "UnsafeSkillContentError"), match="unexpected file type"):
        module.acquire_skill(source, entry, tmp_path / "destination")


def test_accepted_skill_is_placed_readonly(tmp_path: Path) -> None:
    module = load_module()
    source, entry = make_skill_tree(tmp_path, module)
    (source / "scripts" / "schema.xsd").write_text("<schema />\n")
    (source / "scripts" / "template.xml").write_text("<template />\n")
    destination = tmp_path / "destination"

    module.acquire_skill(source, entry, destination)

    assert (destination / "SKILL.md").read_text() == "# Fixture skill\n"
    assert (destination / "scripts" / "schema.xsd").is_file()
    assert (destination / "scripts" / "template.xml").is_file()
    assert (destination / "SKILL.md").stat().st_mode & 0o222 == 0


def module_error(module: object, name: str) -> type[Exception]:
    error = getattr(module, name)
    assert isinstance(error, type)
    assert issubclass(error, Exception)
    return error
