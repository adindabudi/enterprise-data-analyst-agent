from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def tracked_paths_under(path: str) -> list[str]:
    completed = subprocess.run(  # noqa: S603
        ["git", "ls-files", "--", path],  # noqa: S607
        check=True,
        capture_output=True,
        cwd=ROOT,
        text=True,
    )
    return completed.stdout.splitlines()


def test_generated_typescript_contract_artifacts_are_present() -> None:
    generated = ROOT / "packages/contracts/typescript/src/generated"
    expected = {
        "activity-event.d.ts",
        "artifact-record.d.ts",
        "task-manifest.d.ts",
        "task-summary.d.ts",
    }

    assert generated.is_dir()
    assert expected <= {path.name for path in generated.iterdir() if path.is_file()}


def test_proprietary_skill_source_is_not_tracked() -> None:
    assert not tracked_paths_under("skills/vendor")
