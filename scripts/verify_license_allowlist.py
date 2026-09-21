from __future__ import annotations

import argparse
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol, cast

from license_expression import AND, OR, ExpressionError, LicenseExpression, get_spdx_licensing


class SpdxLicensing(Protocol):
    def parse(
        self, expression: str, validate: bool = False, strict: bool = False, simple: bool = False
    ) -> LicenseExpression | None: ...


class CompoundLicenseExpression(Protocol):
    args: tuple[LicenseExpression, ...]


SPDX_LICENSING = cast(SpdxLicensing, get_spdx_licensing())


class UnapprovedLicenseError(ValueError):
    pass


def load_allowlist(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8")
    matches = re.findall(r"```text\n(.*?)\n```", text, flags=re.DOTALL)
    if len(matches) != 1:
        raise ValueError("license allowlist must contain exactly one text block")
    licenses = {line.strip() for line in matches[0].splitlines() if line.strip()}
    if not licenses:
        raise ValueError("license allowlist is empty")
    return licenses


def load_overrides(path: Path) -> dict[str, str]:
    document = object_value(json.loads(path.read_text(encoding="utf-8")), "license overrides")
    overrides: dict[str, str] = {}
    for key, value in document.items():
        component = string_value(key, "license override component")
        if "@" not in component:
            raise ValueError(f"license override must use name@version: {component}")
        overrides[component] = string_value(value, f"license override for {component}")
    return overrides


def verify_license_allowlist(
    sbom: Mapping[str, object], allowlist: set[str], overrides: Mapping[str, str] | None = None
) -> set[str]:
    components = object_list(sbom.get("components"), "SBOM components")
    resolved: set[str] = set()
    for component in components:
        item = object_value(component, "SBOM component")
        if is_ignored_component(item):
            continue
        name = string_value(item.get("name"), "SBOM component name")
        expressions = component_licenses(item, overrides)
        if not expressions:
            raise UnapprovedLicenseError(f"component {name} has no identifiable license")
        for expression in expressions:
            tokens = approved_spdx_tokens(expression, allowlist, component=name)
            resolved.update(tokens)
    return resolved


def component_licenses(component: Mapping[str, object], overrides: Mapping[str, str] | None = None) -> set[str]:
    values = object_list(component.get("licenses", []), "component licenses")
    expressions: set[str] = set()
    for value in values:
        entry = object_value(value, "component license entry")
        direct_expression = entry.get("expression")
        if isinstance(direct_expression, str) and direct_expression:
            expressions.add(direct_expression)
        license_value = entry.get("license")
        if isinstance(license_value, dict):
            license_object = object_value(cast(object, license_value), "component license")
            identifier = license_object.get("id")
            if isinstance(identifier, str) and identifier:
                expressions.add(identifier)
            expression = license_object.get("expression")
            if isinstance(expression, str) and expression:
                expressions.add(expression)
    if not expressions and overrides is not None:
        name = string_value(component.get("name"), "SBOM component name")
        version = string_value(component.get("version", "0"), "SBOM component version")
        override = overrides.get(f"{name}@{version}")
        if override:
            expressions.add(override)
    return expressions


def approved_spdx_tokens(expression: str, allowlist: set[str], *, component: str) -> set[str]:
    try:
        parsed = SPDX_LICENSING.parse(expression, validate=True)
    except ExpressionError as error:
        raise UnapprovedLicenseError(f"component {component} has an unapproved license: {expression}") from error
    if parsed is None:
        raise UnapprovedLicenseError(f"component {component} has an unapproved license: {expression}")
    return approved_expression_tokens(parsed, allowlist, component=component, source=expression)


def approved_expression_tokens(
    expression: LicenseExpression, allowlist: set[str], *, component: str, source: str
) -> set[str]:
    if isinstance(expression, AND):
        operands = cast(CompoundLicenseExpression, expression).args
        approved: set[str] = set()
        for item in operands:
            approved.update(approved_expression_tokens(item, allowlist, component=component, source=source))
        return approved
    if isinstance(expression, OR):
        approved: set[str] = set()
        operands = cast(CompoundLicenseExpression, expression).args
        for item in operands:
            try:
                approved.update(approved_expression_tokens(item, allowlist, component=component, source=source))
            except UnapprovedLicenseError:
                continue
        if approved:
            return approved
    else:
        symbols = cast(set[object], expression.get_symbols())
        tokens = {str(symbol) for symbol in symbols}
        if tokens and tokens <= allowlist:
            return tokens
    raise UnapprovedLicenseError(f"component {component} has an unapproved license: {source}")


def component_records(
    sbom: Mapping[str, object], allowlist: set[str], overrides: Mapping[str, str] | None = None
) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for component in object_list(sbom.get("components"), "SBOM components"):
        item = object_value(component, "SBOM component")
        if is_ignored_component(item):
            continue
        name = string_value(item.get("name"), "SBOM component name")
        version = string_value(item.get("version", "0"), "SBOM component version")
        expressions = sorted(component_licenses(item, overrides))
        if not expressions:
            raise UnapprovedLicenseError(f"component {name} has no identifiable license")
        approved: set[str] = set()
        for expression in expressions:
            approved.update(approved_spdx_tokens(expression, allowlist, component=name))
        records.append({"name": name, "version": version, "licenses": sorted(approved)})
    return records


def is_ignored_component(component: Mapping[str, object]) -> bool:
    return (
        is_first_party_component(component)
        or is_operating_system_inventory(component)
        or component.get("type") == "file"
        or is_base_runtime_binary(component)
        or is_nested_pip_launcher(component)
        or is_nested_dependency_workflow(component)
        or is_nested_javascript_test_fixture(component)
    )


def is_first_party_component(component: Mapping[str, object]) -> bool:
    name = component.get("name")
    purl = component.get("purl")
    return (isinstance(name, str) and (name.startswith("eda-") or name.startswith("@eda/"))) or (
        isinstance(purl, str) and purl.startswith(("pkg:npm/%40eda/", "pkg:npm/@eda/"))
    )


def is_operating_system_inventory(component: Mapping[str, object]) -> bool:
    purl = component.get("purl")
    return component.get("type") == "operating-system" or (isinstance(purl, str) and purl.startswith("pkg:deb/"))


def is_nested_pip_launcher(component: Mapping[str, object]) -> bool:
    if component.get("name") != "Simple Launcher":
        return False
    values = component_properties(component)
    return values.get("syft:package:foundBy") == "pe-binary-package-cataloger" and any(
        name.startswith("syft:location:") and name.endswith(":path") and "/site-packages/pip/_vendor/distlib/" in value
        for name, value in values.items()
    )


def is_base_runtime_binary(component: Mapping[str, object]) -> bool:
    name = component.get("name")
    if name not in {"node", "python"}:
        return False
    values = component_properties(component)
    return values.get("syft:package:foundBy") == "binary-classifier-cataloger" and any(
        property_name.startswith("syft:location:")
        and property_name.endswith(":path")
        and path.startswith(f"/usr/local/bin/{name}")
        for property_name, path in values.items()
    )


def is_nested_dependency_workflow(component: Mapping[str, object]) -> bool:
    values = component_properties(component)
    if values.get("syft:package:foundBy") not in {
        "github-actions-usage-cataloger",
        "github-action-workflow-usage-cataloger",
    }:
        return False
    return any(
        name.startswith("syft:location:")
        and name.endswith(":path")
        and "/node_modules/" in value
        and "/.github/workflows/" in value
        for name, value in values.items()
    )


def is_nested_javascript_test_fixture(component: Mapping[str, object]) -> bool:
    if component.get("version") != "UNKNOWN":
        return False
    values = component_properties(component)
    return values.get("syft:package:foundBy") == "javascript-package-cataloger" and any(
        property_name.startswith("syft:location:")
        and property_name.endswith(":path")
        and "/node_modules/" in path
        and "/test/" in path
        and path.endswith("/package.json")
        for property_name, path in values.items()
    )


def component_properties(component: Mapping[str, object]) -> dict[str, str]:
    properties = object_list(component.get("properties", []), "component properties")
    return {
        string_value(item.get("name"), "component property name"): string_value(
            item.get("value"), "component property value"
        )
        for value in properties
        for item in [object_value(value, "component property")]
    }


def write_notice(path: Path, licenses: set[str], components: list[dict[str, object]]) -> None:
    existing = path.read_text(encoding="utf-8") if path.exists() else "# Third-Party Notices\n"
    marker = "<!-- GENERATED LICENSE SET START -->"
    end_marker = "<!-- GENERATED LICENSE SET END -->"
    unique_components = {
        (
            str(component.get("name")),
            str(component.get("version")),
            tuple(sorted(cast(list[str], component["licenses"]))),
        ): component
        for component in components
    }
    generated = (
        f"{marker}\n\nResolved SPDX license set:\n\n"
        + "\n".join(f"- {license_name}" for license_name in sorted(licenses))
        + "\n\nResolved components:\n\n"
        + "\n".join(
            f"- {string_value(component.get('name'), 'notice component name')} "
            f"{string_value(component.get('version'), 'notice component version')}: "
            f"{', '.join(sorted(cast(list[str], component.get('licenses', []))))}"
            for _, component in sorted(unique_components.items())
        )
        + f"\n\n{end_marker}\n"
    )
    if marker in existing and end_marker in existing:
        prefix, remainder = existing.split(marker, maxsplit=1)
        _, suffix = remainder.split(end_marker, maxsplit=1)
        content = prefix + generated + suffix.lstrip("\n")
    else:
        content = existing.rstrip() + "\n\n" + generated
    path.write_text(content, encoding="utf-8")


def object_list(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array")
    return cast(list[object], value)


def object_value(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return cast(dict[str, object], value)


def string_value(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a nonempty string")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify CycloneDX SBOM licenses against the reviewed allowlist.")
    parser.add_argument("--sbom-dir", type=Path, required=True)
    parser.add_argument("--allowlist", type=Path, default=Path("docs/security/license-allowlist.md"))
    parser.add_argument("--exclude-sbom", action="append", default=[])
    parser.add_argument("--overrides", type=Path)
    parser.add_argument("--notices", type=Path, default=Path("THIRD_PARTY_NOTICES.md"))
    arguments = parser.parse_args(argv)
    try:
        excluded_sboms = set(arguments.exclude_sbom)
        sbom_paths = sorted(path for path in arguments.sbom_dir.glob("*.cdx.json") if path.name not in excluded_sboms)
        if not sbom_paths:
            raise ValueError("SBOM directory contains no CycloneDX JSON files")
        licenses: set[str] = set()
        components: list[dict[str, object]] = []
        allowlist = load_allowlist(arguments.allowlist)
        overrides = load_overrides(arguments.overrides) if arguments.overrides else {}
        for path in sbom_paths:
            document = object_value(json.loads(path.read_text(encoding="utf-8")), "SBOM document")
            licenses.update(verify_license_allowlist(document, allowlist, overrides))
            components.extend(component_records(document, allowlist, overrides))
        write_notice(arguments.notices, licenses, components)
    except (OSError, ValueError, json.JSONDecodeError, UnapprovedLicenseError) as error:
        print(f"FAIL: {error}")
        return 1
    print(f"PASS: verified {len(licenses)} SPDX licenses")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
