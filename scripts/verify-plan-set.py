from __future__ import annotations

import re
import sys
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROADMAP_NAME = "2026-07-23-enterprise-data-analyst-roadmap.md"
PLAN_DATE = "2026-07-23"
PLAN_PREFIXES = (
    *((f"{PLAN_DATE}-{number:02d}-", f"{number:02d}") for number in range(1, 11)),
    ("2026-07-24-08b-", "2026-07-24-08b-"),
)
PLAN_FILENAME_PATTERN = re.compile(r"(?:2026-07-23-(?:0[1-9]|10)|2026-07-24-08b)-[A-Za-z0-9][A-Za-z0-9_.-]*\.md")
FENCE_PATTERN = re.compile(r"^\s*```", re.MULTILINE)
TITLE_PATTERN = re.compile(r"^#\s+.+Implementation Plan\s*$", re.MULTILINE)
AGENT_HEADER = "> **For agentic workers:** REQUIRED SUB-SKILL:"
STEP_PATTERN = re.compile(r"^- \[ \] \*\*Step\s+\d+:", re.MULTILINE)
PLACEHOLDER_LABELS = (
    "TBD",
    "TODO:",
    "implement later",
    "fill in details",
    "Add appropriate error handling",
    "add validation",
    "handle edge cases",
    "Write tests for the above",
    "Similar to Task",
)


def placeholder_pattern(label: str) -> re.Pattern[str]:
    return re.compile(rf"^\s*(?:[-*]\s+)?(?:\*\*)?{re.escape(label)}(?:\*\*)?[.!\s]*$", re.IGNORECASE | re.MULTILINE)


def markdown_section(text: str, heading: str) -> str | None:
    heading_pattern = re.compile(rf"^##\s+{re.escape(heading)}\s*$", re.IGNORECASE | re.MULTILINE)
    match = heading_pattern.search(text)
    if match is None:
        return None

    next_heading = re.compile(r"^##\s+", re.MULTILINE).search(text, match.end())
    return text[match.end() : next_heading.start() if next_heading else len(text)]


def plan_paths(plan_dir: Path) -> tuple[list[Path], list[str]]:
    paths: list[Path] = []
    errors: list[str] = []
    for prefix, label in PLAN_PREFIXES:
        matches = sorted(plan_dir.glob(f"{prefix}*.md"))
        if len(matches) != 1:
            errors.append(f"FAIL: expected exactly one plan for prefix {label}; found {len(matches)}")
        else:
            paths.append(matches[0])
    return paths, errors


def validate_plan(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    errors: list[str] = []
    if TITLE_PATTERN.search(text) is None:
        errors.append(f"FAIL: {path.name}: title must end with 'Implementation Plan'")
    if AGENT_HEADER not in text:
        errors.append(f"FAIL: {path.name}: missing agentic-worker header")
    if STEP_PATTERN.search(text) is None:
        errors.append(f"FAIL: {path.name}: missing checkbox Step")
    if len(FENCE_PATTERN.findall(text)) % 2:
        errors.append(f"FAIL: {path.name}: unbalanced Markdown fences")
    for label in PLACEHOLDER_LABELS:
        if placeholder_pattern(label).search(text) is not None:
            errors.append(f"FAIL: {path.name}: forbidden placeholder '{label}'")
    return errors


def roadmap_errors(plan_dir: Path, paths: Sequence[Path]) -> list[str]:
    roadmap_path = plan_dir / ROADMAP_NAME
    if not roadmap_path.is_file():
        return [f"FAIL: missing roadmap {ROADMAP_NAME}"]

    text = roadmap_path.read_text(encoding="utf-8")
    errors: list[str] = []
    inventory = markdown_section(text, "Plan Inventory")
    if inventory is None:
        return ["FAIL: roadmap is missing the Plan Inventory section"]

    inventory_files = set(PLAN_FILENAME_PATTERN.findall(inventory))
    for path in paths:
        if path.name not in inventory_files:
            errors.append(f"FAIL: roadmap inventory does not reference {path.name}")

    traceability = markdown_section(text, "Spec Traceability")
    if traceability is None:
        return [*errors, "FAIL: roadmap is missing the Spec Traceability section"]

    mapped_sections: set[int] = set()
    for line in traceability.splitlines():
        if not line.startswith("|"):
            continue
        first_cell = line.split("|", maxsplit=2)[1].strip()
        for match in re.finditer(r"\b(\d+)(?:-(\d+))?\b", first_cell):
            start = int(match.group(1))
            end = int(match.group(2) or start)
            mapped_sections.update(range(start, end + 1))

    missing_sections = sorted(set(range(1, 28)) - mapped_sections)
    if missing_sections:
        joined_sections = ", ".join(str(number) for number in missing_sections)
        errors.append(f"FAIL: roadmap spec traceability is missing design sections: {joined_sections}")
    return errors


def main(arguments: Sequence[str] | None = None) -> int:
    values = list(sys.argv[1:] if arguments is None else arguments)
    if len(values) > 1:
        print("Usage: python3 scripts/verify-plan-set.py [plan_dir]")
        return 2

    plan_dir = Path(values[0]) if values else ROOT / "docs/superpowers/plans"
    if not plan_dir.is_dir():
        print(f"FAIL: plan directory does not exist: {plan_dir}")
        return 1

    paths, errors = plan_paths(plan_dir)
    if errors:
        print("\n".join(errors))
        return 1

    for path in paths:
        errors.extend(validate_plan(path))
    errors.extend(roadmap_errors(plan_dir, paths))
    if errors:
        print("\n".join(errors))
        return 1

    print(
        "PASS: 11 implementation plans, 27 design sections mapped, no placeholders, all referenced plan files present"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
