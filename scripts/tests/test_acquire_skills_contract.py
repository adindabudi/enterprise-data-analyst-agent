from __future__ import annotations

import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "scripts" / "document_skills_contract.py"
LOCK_PATH = ROOT / "skills.lock.json"


def load_module():
    specification = spec_from_file_location("document_skills_contract", MODULE_PATH)
    assert specification is not None
    assert specification.loader is not None
    module = module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


def test_lock_file_pins_exact_commit_and_allowlisted_skill_trees() -> None:
    module = load_module()
    lock = module.load_lock(LOCK_PATH)

    assert lock.source == "https://github.com/anthropics/skills"
    assert lock.commit == "1f630fdf9259cec4a14913127dfd7c3b69ef72eb"
    assert {entry.name for entry in lock.skills} == {"docx", "pdf", "pptx", "xlsx", "web-artifacts-builder"}
    for entry in lock.skills:
        assert entry.license_url.startswith("https://")
        assert entry.terms_url.startswith("https://")
        assert entry.allowed_paths[:3] == ("SKILL.md", "LICENSE.txt", "scripts/")
    web = next(entry for entry in lock.skills if entry.name == "web-artifacts-builder")
    assert web.license_sha256 == "bc6b3af2f331cbc7fb0da1344efb2cbe5877a31498b4d70dbc7000f3405a1362"
    assert "scripts/shadcn-components.tar.gz" in web.allowed_paths


def test_missing_terms_acceptance_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_module()
    monkeypatch.delenv("EDA_DOCUMENT_TERMS_ACCEPTED", raising=False)

    with pytest.raises(module.TermsNotAcceptedError):
        module.require_terms_acceptance(LOCK_PATH)


def test_terms_acceptance_requires_the_exact_lock_hash(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_module()
    monkeypatch.setenv("EDA_DOCUMENT_TERMS_ACCEPTED", "wrong-hash")

    with pytest.raises(module.TermsNotAcceptedError):
        module.require_terms_acceptance(LOCK_PATH)


def test_web_pack_is_available_without_document_terms(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_module()
    monkeypatch.delenv("EDA_DOCUMENT_TERMS_ACCEPTED", raising=False)

    _, entries = module.load_skill_pack(LOCK_PATH, "web")

    assert [entry.name for entry in entries] == ["web-artifacts-builder"]


def test_document_pack_still_requires_exact_terms_acceptance(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_module()
    monkeypatch.delenv("EDA_DOCUMENT_TERMS_ACCEPTED", raising=False)

    with pytest.raises(module.TermsNotAcceptedError):
        module.load_skill_pack(LOCK_PATH, "documents")
