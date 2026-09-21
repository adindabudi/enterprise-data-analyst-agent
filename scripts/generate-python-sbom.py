from __future__ import annotations

import argparse
import hashlib
import json
from importlib.metadata import PackageNotFoundError, metadata
from pathlib import Path
from typing import Any

from license_expression import ExpressionError, get_spdx_licensing
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

LICENSING: Any = get_spdx_licensing()


def build_sbom(requirements: str) -> dict[str, object]:
    components: dict[str, dict[str, object]] = {}
    for line in requirements.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        requirement = Requirement(line)
        if requirement.marker is not None and not requirement.marker.evaluate():
            continue
        specifiers = tuple(requirement.specifier)
        if requirement.url or len(specifiers) != 1 or specifiers[0].operator != "==" or "*" in specifiers[0].version:
            raise ValueError(f"runtime requirement must be exactly pinned: {requirement.name}")
        name = canonicalize_name(requirement.name)
        document = metadata(name)
        version = document.get("Version")
        if version != specifiers[0].version:
            raise ValueError(f"installed version mismatch for {name}; run uv sync --all-packages --frozen")
        component: dict[str, object] = {
            "type": "library",
            "name": name,
            "version": version,
            "purl": f"pkg:pypi/{name}@{version}",
        }
        expression = document.get("License-Expression")
        license_text = document.get("License")
        if expression:
            component["licenses"] = [{"expression": expression}]
        elif license_text:
            try:
                parsed: object = LICENSING.parse(license_text, validate=True)
            except ExpressionError:
                component["licenses"] = [{"license": {"name": license_text}}]
            else:
                if parsed is not None:
                    component["licenses"] = [{"expression": str(parsed)}]
        components[name] = component
    if not components:
        raise ValueError("runtime requirements contain no packages for this platform")
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "version": 1,
        "metadata": {
            "properties": [
                {"name": "eda:requirements:sha256", "value": hashlib.sha256(requirements.encode()).hexdigest()}
            ]
        },
        "components": [components[name] for name in sorted(components)],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate a CycloneDX SBOM for exactly pinned installed Python packages."
    )
    parser.add_argument("--requirements", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        sbom = build_sbom(arguments.requirements.read_text(encoding="utf-8"))
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(json.dumps(sbom, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError, PackageNotFoundError) as error:
        print(f"FAIL: {error}")
        return 1
    print("PASS: generated lock-bound Python runtime SBOM")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
