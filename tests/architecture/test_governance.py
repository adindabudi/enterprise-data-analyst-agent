from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REQUIRED_FILES = {
    "LICENSE",
    "NOTICE.md",
    "SUPPORT.md",
    "SECURITY.md",
    "CONTRIBUTING.md",
    "CODE_OF_CONDUCT.md",
    "THIRD_PARTY_NOTICES.md",
}


def test_governance_documents_are_present_with_required_statements() -> None:
    assert all((ROOT / path).is_file() for path in REQUIRED_FILES)

    assert "community-supported" in (ROOT / "SUPPORT.md").read_text(encoding="utf-8")
    assert "Microsoft product support" in (ROOT / "SUPPORT.md").read_text(encoding="utf-8")
    assert "Acquired skill content is not distributed by this repository" in (ROOT / "NOTICE.md").read_text(
        encoding="utf-8"
    )
