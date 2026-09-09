from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
VERIFIER = ROOT / "scripts/verify-plan-set.py"
ROADMAP_NAME = "2026-07-23-enterprise-data-analyst-roadmap.md"
PLAN_8B_NAME = "2026-07-24-08b-fabric-ontology-pack.md"
AGENT_HEADER = "> **For agentic workers:** REQUIRED SUB-SKILL: Use a required execution skill."


def plan_filename(number: int) -> str:
    return f"2026-07-23-{number:02d}-plan-{number}.md"


def plan_content(*, title: str = "Plan Implementation Plan", include_agent_header: bool = True) -> str:
    agent_header = f"{AGENT_HEADER}\n\n" if include_agent_header else ""
    return f"# {title}\n\n{agent_header}- [ ] **Step 1: Verify the plan**\n\n```text\ncomplete\n```\n"


def roadmap_content(plan_names: list[str], *, include_section_27: bool = True) -> str:
    inventory = "\n".join(f"| {number} | `{name}` | Work |" for number, name in enumerate(plan_names, start=1))
    sections = "\n".join(
        f"| {number} scope | Plan 1 |" for number in range(1, 28) if include_section_27 or number != 27
    )
    return (
        "# Enterprise Data Analyst Delivery Roadmap Implementation Plan\n\n"
        "## Plan Inventory\n\n"
        "| Order | Plan file | Working increment |\n"
        "| ---: | --- | --- |\n"
        f"{inventory}\n\n"
        "## Spec Traceability\n\n"
        "| Design sections | Owning plan |\n"
        "| --- | --- |\n"
        f"{sections}\n"
    )


def create_valid_plan_set(tmp_path: Path) -> Path:
    plan_dir = tmp_path / "plans"
    plan_dir.mkdir()
    plan_names = [plan_filename(number) for number in range(1, 11)] + [PLAN_8B_NAME]
    for name in plan_names:
        (plan_dir / name).write_text(plan_content(), encoding="utf-8")
    (plan_dir / ROADMAP_NAME).write_text(roadmap_content(plan_names), encoding="utf-8")
    return plan_dir


def run_verifier(plan_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, str(VERIFIER), str(plan_dir)],
        check=False,
        capture_output=True,
        text=True,
    )


def add_duplicate_plan(plan_dir: Path) -> None:
    (plan_dir / "2026-07-23-01-duplicate.md").write_text(plan_content(), encoding="utf-8")


def remove_plan_ten(plan_dir: Path) -> None:
    (plan_dir / plan_filename(10)).unlink()


def replace_with_invalid_title(plan_dir: Path) -> None:
    (plan_dir / plan_filename(1)).write_text(plan_content(title="Plan Overview"), encoding="utf-8")


def replace_without_agent_header(plan_dir: Path) -> None:
    (plan_dir / plan_filename(1)).write_text(plan_content(include_agent_header=False), encoding="utf-8")


def replace_without_step(plan_dir: Path) -> None:
    (plan_dir / plan_filename(1)).write_text(
        f"# Plan Implementation Plan\n\n{AGENT_HEADER}\n\nNo steps here.\n", encoding="utf-8"
    )


def replace_with_unbalanced_fence(plan_dir: Path) -> None:
    (plan_dir / plan_filename(1)).write_text(plan_content().removesuffix("```\n"), encoding="utf-8")


def replace_with_placeholder(plan_dir: Path) -> None:
    (plan_dir / plan_filename(1)).write_text(f"{plan_content()}\nTBD\n", encoding="utf-8")


INVALID_MUTATIONS: list[tuple[Callable[[Path], None], str]] = [
    (add_duplicate_plan, "expected exactly one plan for prefix 01"),
    (remove_plan_ten, "expected exactly one plan for prefix 10"),
    (replace_with_invalid_title, "title must end with 'Implementation Plan'"),
    (replace_without_agent_header, "missing agentic-worker header"),
    (replace_without_step, "missing checkbox Step"),
    (replace_with_unbalanced_fence, "unbalanced Markdown fences"),
    (replace_with_placeholder, "forbidden placeholder 'TBD'"),
]


def test_valid_plan_set_passes(tmp_path: Path) -> None:
    result = run_verifier(create_valid_plan_set(tmp_path))

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == (
        "PASS: 11 implementation plans, 27 design sections mapped, no placeholders, all referenced plan files present"
    )


@pytest.mark.parametrize(
    ("mutation", "expected_message"),
    INVALID_MUTATIONS,
)
def test_invalid_plan_content_fails(tmp_path: Path, mutation: Callable[[Path], None], expected_message: str) -> None:
    plan_dir = create_valid_plan_set(tmp_path)
    mutation(plan_dir)

    result = run_verifier(plan_dir)

    assert result.returncode != 0
    assert expected_message in result.stdout


def test_plan_absent_from_roadmap_inventory_fails(tmp_path: Path) -> None:
    plan_dir = create_valid_plan_set(tmp_path)
    plan_names = [plan_filename(number) for number in range(1, 11)]
    (plan_dir / ROADMAP_NAME).write_text(roadmap_content(plan_names), encoding="utf-8")

    result = run_verifier(plan_dir)

    assert result.returncode != 0
    assert f"roadmap inventory does not reference {PLAN_8B_NAME}" in result.stdout


def test_missing_plan_8b_fails(tmp_path: Path) -> None:
    plan_dir = create_valid_plan_set(tmp_path)
    (plan_dir / PLAN_8B_NAME).unlink()

    result = run_verifier(plan_dir)

    assert result.returncode != 0
    assert "expected exactly one plan for prefix 2026-07-24-08b-" in result.stdout


def test_missing_section_27_mapping_fails(tmp_path: Path) -> None:
    plan_dir = create_valid_plan_set(tmp_path)
    plan_names = [plan_filename(number) for number in range(1, 11)] + [PLAN_8B_NAME]
    (plan_dir / ROADMAP_NAME).write_text(roadmap_content(plan_names, include_section_27=False), encoding="utf-8")

    result = run_verifier(plan_dir)

    assert result.returncode != 0
    assert "roadmap spec traceability is missing design sections: 27" in result.stdout
