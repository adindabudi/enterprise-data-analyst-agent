from __future__ import annotations

import json
from pathlib import Path

import pytest
from eda_worker.web_artifacts.readiness import WebArtifactSettings, require_web_artifact_skill


def write_web_skill(root: Path) -> Path:
    skill = root / "web-artifacts-builder"
    scripts = skill / "scripts"
    scripts.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: web-artifacts-builder\n---\n", encoding="utf-8")
    (skill / "LICENSE.txt").write_text("Apache License 2.0\n", encoding="utf-8")
    (skill / ".acquired.json").write_text(
        json.dumps(
            {
                "commit": "1f630fdf9259cec4a14913127dfd7c3b69ef72eb",
                "licenseSha256": "b" * 64,
                "skillMdSha256": "a" * 64,
                "verifiedAt": "2026-09-01T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    (scripts / "init-artifact.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    (scripts / "bundle-artifact.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    (scripts / "shadcn-components.tar.gz").write_bytes(b"fixture")
    return skill


def test_complete_reviewed_web_skill_is_mandatory_and_independent(tmp_path: Path) -> None:
    skill = write_web_skill(tmp_path)

    resolved = require_web_artifact_skill(WebArtifactSettings(skill_root=tmp_path))

    assert resolved == skill


@pytest.mark.parametrize(
    "missing",
    [
        "SKILL.md",
        "LICENSE.txt",
        ".acquired.json",
        "scripts/init-artifact.sh",
        "scripts/bundle-artifact.sh",
        "scripts/shadcn-components.tar.gz",
    ],
)
def test_incomplete_web_skill_fails_closed(tmp_path: Path, missing: str) -> None:
    skill = write_web_skill(tmp_path)
    (skill / missing).unlink()

    with pytest.raises(RuntimeError, match="Web Artifact Pack"):
        require_web_artifact_skill(WebArtifactSettings(skill_root=tmp_path))
