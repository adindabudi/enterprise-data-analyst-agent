from __future__ import annotations

import json
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_PYTHON = (3, 12)
EXPECTED_NODE_MAJOR = 24


def command_version(command: list[str]) -> str:
    # Commands are fixed local toolchain probes, never user-controlled input.
    completed = subprocess.run(command, check=True, capture_output=True, text=True)  # noqa: S603
    return completed.stdout.strip()


def main() -> int:
    required = [
        ROOT / ".python-version",
        ROOT / ".nvmrc",
        ROOT / "pyproject.toml",
        ROOT / "package.json",
        ROOT / "Makefile",
    ]
    missing = [str(path.relative_to(ROOT)) for path in required if not path.exists()]
    if missing:
        print(f"FAIL: missing required files: {', '.join(missing)}")
        return 1

    if sys.version_info[:2] != EXPECTED_PYTHON:
        print(f"FAIL: Python {EXPECTED_PYTHON[0]}.{EXPECTED_PYTHON[1]} required")
        return 1

    node_version = command_version(["node", "--version"]).lstrip("v")
    if int(node_version.split(".", maxsplit=1)[0]) != EXPECTED_NODE_MAJOR:
        print(f"FAIL: Node {EXPECTED_NODE_MAJOR}.x required")
        return 1

    with (ROOT / "pyproject.toml").open("rb") as stream:
        python_config = tomllib.load(stream)
    node_config = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))

    if python_config["project"]["requires-python"] != "==3.12.*":
        print("FAIL: pyproject.toml must pin Python to ==3.12.*")
        return 1
    if node_config["engines"]["node"] != "24.x":
        print("FAIL: package.json must pin Node to 24.x")
        return 1

    print(f"PASS: Python {sys.version.split()[0]}, Node {node_version}, locked workspace manifests present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
