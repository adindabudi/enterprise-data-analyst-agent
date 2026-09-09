from __future__ import annotations

import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[4]
MODULE_PATH = ROOT / "services" / "worker" / "document-skills" / "build-bundles.py"


def load_module() -> Any:
    specification = spec_from_file_location("document_skill_bundles", MODULE_PATH)
    assert specification is not None
    assert specification.loader is not None
    module = module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


def make_skill_tree(tmp_path: Path) -> Path:
    root = tmp_path / "skills"
    for name in ("docx", "pdf", "pptx", "xlsx"):
        (root / name / "scripts").mkdir(parents=True)
        (root / name / "SKILL.md").write_text(f"# {name}\n")
        (root / name / "scripts" / "validate.py").write_text("print('valid')\n")
        (root / name / ".acquired.json").write_text("{}\n")
        (root / name / ".hidden").write_text("excluded\n")
    return root


def test_each_skill_is_packaged_into_a_deterministic_zip(tmp_path: Path) -> None:
    module = load_module()
    skills = make_skill_tree(tmp_path)

    bundles = module.build_bundles(skills, tmp_path / "bundles")
    repeated = module.build_bundles(skills, tmp_path / "bundles-again")

    assert {bundle.path.name for bundle in bundles} == {"docx.zip", "pdf.zip", "pptx.zip", "xlsx.zip"}
    assert {bundle.sha256 for bundle in bundles} == {bundle.sha256 for bundle in repeated}


def test_bundle_excludes_dotfiles_and_acquisition_metadata(tmp_path: Path) -> None:
    module = load_module()
    bundles = module.build_bundles(make_skill_tree(tmp_path), tmp_path / "bundles")

    names = module.zip_member_names(next(bundle.path for bundle in bundles if bundle.path.name == "docx.zip"))
    assert ".acquired.json" not in names
    assert all(not Path(name).name.startswith(".") for name in names)
