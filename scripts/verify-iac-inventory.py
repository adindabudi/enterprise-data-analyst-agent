from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

SECRET_PATTERN = re.compile(r"(?:connection|string|key|password|sas|secret|token)", re.IGNORECASE)
SINGLETON_RESOURCE_TYPES = frozenset(
    {
        "Microsoft.CognitiveServices/accounts",
        "Microsoft.CognitiveServices/accounts/projects",
        "Microsoft.CognitiveServices/accounts/deployments",
        "Microsoft.Cache/redisEnterprise",
        "Microsoft.Cache/redisEnterprise/databases",
        "Microsoft.DocumentDB/databaseAccounts",
        "Microsoft.Storage/storageAccounts",
        "Microsoft.App/managedEnvironments",
        "Microsoft.App/sandboxGroups",
    }
)


@dataclass(frozen=True)
class BicepInventory:
    resource_types: frozenset[str]
    api_versions: tuple[str, ...]
    modules: tuple[str, ...]
    resource_counts: Counter[str]

    def count(self, resource_type: str) -> int:
        return self.resource_counts[resource_type]


def build_bicep_template(bicep_dir: Path) -> dict[str, Any]:
    main_file = bicep_dir / "main.bicep"
    if not main_file.is_file():
        raise ValueError(f"missing Bicep entrypoint: {main_file}")

    with tempfile.TemporaryDirectory() as temporary_directory:
        output_file = Path(temporary_directory) / "main.json"
        # Azure CLI command and arguments are fixed; only local file paths are supplied.
        completed = subprocess.run(  # noqa: S603
            ["az", "bicep", "build", "--file", str(main_file), "--outfile", str(output_file)],  # noqa: S607
            capture_output=True,
            check=False,
            text=True,
        )
        if completed.returncode != 0:
            details = "\n".join(part for part in (completed.stdout.strip(), completed.stderr.strip()) if part)
            raise ValueError(f"Bicep build failed:\n{details}")
        return cast(dict[str, Any], json.loads(output_file.read_text(encoding="utf-8")))


def iter_resources(template: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    resources = template.get("resources", [])
    if isinstance(resources, list):
        values: list[Any] = cast(list[Any], resources)
    elif isinstance(resources, Mapping):
        resource_mapping = cast(Mapping[str, Any], resources)
        values = list(resource_mapping.values())
    else:
        raise ValueError("template resources must be an array or object")

    for value in values:
        if not isinstance(value, Mapping):
            raise ValueError("template resource must be an object")
        resource = cast(Mapping[str, Any], value)
        yield resource
        properties_value = resource.get("properties")
        if not isinstance(properties_value, Mapping):
            continue
        properties = cast(Mapping[str, Any], properties_value)
        nested_template = properties.get("template")
        if isinstance(nested_template, Mapping):
            yield from iter_resources(cast(Mapping[str, Any], nested_template))


def module_names(bicep_dir: Path) -> tuple[str, ...]:
    modules_directory = bicep_dir / "modules"
    if not modules_directory.is_dir():
        return ()
    return tuple(sorted(path.relative_to(bicep_dir).as_posix() for path in modules_directory.rglob("*.bicep")))


def validate_outputs(template: Mapping[str, Any]) -> None:
    outputs_value = template.get("outputs", {})
    if not isinstance(outputs_value, Mapping):
        raise ValueError("template outputs must be an object")
    outputs = cast(Mapping[str, Any], outputs_value)
    for name, definition in outputs.items():
        if SECRET_PATTERN.search(str(name)):
            raise ValueError(f"secret-looking output is not allowed: {name}")
        if isinstance(definition, Mapping):
            output_definition = cast(Mapping[str, Any], definition)
            value = output_definition.get("value")
            if SECRET_PATTERN.search(json.dumps(value, sort_keys=True)):
                raise ValueError(f"secret-looking output value is not allowed: {name}")


def load_bicep_inventory(bicep_dir: Path) -> BicepInventory:
    template = build_bicep_template(bicep_dir)
    validate_outputs(template)
    resources = tuple(iter_resources(template))

    resource_counts = Counter(str(resource.get("type", "")) for resource in resources)
    duplicates = sorted(
        resource_type
        for resource_type, count in resource_counts.items()
        if resource_type in SINGLETON_RESOURCE_TYPES and count > 1
    )
    if duplicates:
        raise ValueError(f"duplicate singleton resources: {', '.join(duplicates)}")

    api_versions = tuple(sorted({str(resource["apiVersion"]) for resource in resources if "apiVersion" in resource}))
    return BicepInventory(
        resource_types=frozenset(resource_counts),
        api_versions=api_versions,
        modules=module_names(bicep_dir),
        resource_counts=resource_counts,
    )


def parse_arguments(arguments: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate the compiled Core Data Pack IaC topology.")
    parser.add_argument("bicep_dir", nargs="?", type=Path, default=Path("infra/bicep"))
    return parser.parse_args(arguments)


def main(arguments: Sequence[str] | None = None) -> int:
    parsed = parse_arguments(sys.argv[1:] if arguments is None else arguments)
    try:
        inventory = load_bicep_inventory(parsed.bicep_dir)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"FAIL: {error}")
        return 1

    print(
        json.dumps(
            {
                "apiVersions": inventory.api_versions,
                "modules": inventory.modules,
                "resourceTypes": sorted(inventory.resource_types),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
