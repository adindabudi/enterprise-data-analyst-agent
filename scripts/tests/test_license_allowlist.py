from __future__ import annotations

import json
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "scripts" / "verify_license_allowlist.py"
ALLOWLIST_PATH = ROOT / "docs" / "security" / "license-allowlist.md"


def load_module() -> Any:
    specification = spec_from_file_location("verify_license_allowlist", MODULE_PATH)
    assert specification is not None
    assert specification.loader is not None
    module = module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


def sbom(license_expression: str | None) -> dict[str, object]:
    component: dict[str, object] = {"name": "fixture-component", "version": "1.0.0"}
    if license_expression is not None:
        component["licenses"] = [{"license": {"expression": license_expression}}]
    return {"bomFormat": "CycloneDX", "components": [component]}


def test_undeclared_license_fails_the_gate() -> None:
    module = load_module()

    with pytest.raises(module.UnapprovedLicenseError):
        module.verify_license_allowlist(sbom("Proprietary"), module.load_allowlist(ALLOWLIST_PATH))


def test_missing_license_fails_closed() -> None:
    module = load_module()

    with pytest.raises(module.UnapprovedLicenseError, match="identifiable license"):
        module.verify_license_allowlist(sbom(None), module.load_allowlist(ALLOWLIST_PATH))


def test_nested_pip_launcher_binary_is_not_a_standalone_component() -> None:
    module = load_module()
    document = {
        "bomFormat": "CycloneDX",
        "components": [
            {
                "type": "application",
                "name": "Simple Launcher",
                "version": "1.1.0.14",
                "properties": [
                    {"name": "syft:package:foundBy", "value": "pe-binary-package-cataloger"},
                    {
                        "name": "syft:location:0:path",
                        "value": "/usr/local/lib/python3.12/site-packages/pip/_vendor/distlib/t64.exe",
                    },
                ],
            }
        ],
    }

    assert module.verify_license_allowlist(document, module.load_allowlist(ALLOWLIST_PATH)) == set()


@pytest.mark.parametrize("name", ["eda-api", "eda-runtime-state", "@eda/web", "@eda/contracts"])
def test_first_party_workspace_components_are_not_third_party_notices(name: str) -> None:
    module = load_module()
    document = {"bomFormat": "CycloneDX", "components": [{"name": name, "version": "0.1.0"}]}

    assert module.verify_license_allowlist(document, module.load_allowlist(ALLOWLIST_PATH)) == set()
    assert module.component_records(document, module.load_allowlist(ALLOWLIST_PATH)) == []


@pytest.mark.parametrize(
    "name,purl",
    [("web", "pkg:npm/%40eda/web@0.1.0"), ("typescript", "pkg:npm/%40eda/contracts@0.1.0")],
)
def test_npm_workspace_purl_identifies_first_party_component(name: str, purl: str) -> None:
    module = load_module()
    document = {"bomFormat": "CycloneDX", "components": [{"name": name, "version": "0.1.0", "purl": purl}]}

    assert module.verify_license_allowlist(document, module.load_allowlist(ALLOWLIST_PATH)) == set()


@pytest.mark.parametrize(
    "cataloger",
    ["github-actions-usage-cataloger", "github-action-workflow-usage-cataloger"],
)
def test_github_workflows_vendored_inside_dependencies_are_not_packages(cataloger: str) -> None:
    module = load_module()
    document = {
        "bomFormat": "CycloneDX",
        "components": [
            {
                "type": "library",
                "name": "actions/checkout",
                "version": "v6",
                "properties": [
                    {"name": "syft:package:foundBy", "value": cataloger},
                    {
                        "name": "syft:location:0:path",
                        "value": "/opt/eda/node_modules/example/.github/workflows/ci.yml",
                    },
                ],
            }
        ],
    }

    assert module.verify_license_allowlist(document, module.load_allowlist(ALLOWLIST_PATH)) == set()


@pytest.mark.parametrize(
    "component",
    [
        {"type": "operating-system", "name": "debian", "version": "12"},
        {
            "type": "library",
            "name": "adduser",
            "version": "3.134",
            "purl": "pkg:deb/debian/adduser@3.134?arch=all",
            "licenses": [{"license": {"id": "GPL-2.0-or-later"}}],
        },
    ],
)
def test_operating_system_inventory_is_outside_application_license_gate(component: dict[str, object]) -> None:
    module = load_module()
    document = {"bomFormat": "CycloneDX", "components": [component]}

    assert module.verify_license_allowlist(document, module.load_allowlist(ALLOWLIST_PATH)) == set()
    assert module.component_records(document, module.load_allowlist(ALLOWLIST_PATH)) == []


def test_file_inventory_is_not_an_independently_licensed_dependency() -> None:
    module = load_module()
    document = {
        "bomFormat": "CycloneDX",
        "components": [{"type": "file", "name": "/usr/share/zoneinfo/Asia/Jakarta"}],
    }

    assert module.verify_license_allowlist(document, module.load_allowlist(ALLOWLIST_PATH)) == set()
    assert module.component_records(document, module.load_allowlist(ALLOWLIST_PATH)) == []


@pytest.mark.parametrize("name,path", [("node", "/usr/local/bin/node"), ("python", "/usr/local/bin/python3.12")])
def test_base_runtime_binary_is_not_a_standalone_dependency(name: str, path: str) -> None:
    module = load_module()
    document = {
        "bomFormat": "CycloneDX",
        "components": [
            {
                "type": "application",
                "name": name,
                "version": "1.0.0",
                "properties": [
                    {"name": "syft:package:foundBy", "value": "binary-classifier-cataloger"},
                    {"name": "syft:location:0:path", "value": path},
                ],
            }
        ],
    }

    assert module.verify_license_allowlist(document, module.load_allowlist(ALLOWLIST_PATH)) == set()


def test_nested_javascript_test_fixture_is_not_a_standalone_dependency() -> None:
    module = load_module()
    document = {
        "bomFormat": "CycloneDX",
        "components": [
            {
                "type": "library",
                "name": "baz",
                "version": "UNKNOWN",
                "properties": [
                    {"name": "syft:package:foundBy", "value": "javascript-package-cataloger"},
                    {
                        "name": "syft:location:0:path",
                        "value": "/opt/eda/node_modules/resolve/test/resolver/baz/package.json",
                    },
                ],
            }
        ],
    }

    assert module.verify_license_allowlist(document, module.load_allowlist(ALLOWLIST_PATH)) == set()


def test_approved_license_passes() -> None:
    module = load_module()

    assert module.verify_license_allowlist(sbom("MIT"), module.load_allowlist(ALLOWLIST_PATH)) == {"MIT"}


def test_exact_reviewed_override_supplies_missing_license() -> None:
    module = load_module()
    document = {"bomFormat": "CycloneDX", "components": [{"name": "example-package", "version": "1.2.3"}]}
    overrides = {"example-package@1.2.3": "MIT"}

    assert module.verify_license_allowlist(document, module.load_allowlist(ALLOWLIST_PATH), overrides) == {"MIT"}
    assert module.component_records(document, module.load_allowlist(ALLOWLIST_PATH), overrides) == [
        {"name": "example-package", "version": "1.2.3", "licenses": ["MIT"]}
    ]


def test_reviewed_override_does_not_apply_to_another_version() -> None:
    module = load_module()
    document = {"bomFormat": "CycloneDX", "components": [{"name": "example-package", "version": "1.2.4"}]}

    with pytest.raises(module.UnapprovedLicenseError, match="identifiable license"):
        module.verify_license_allowlist(
            document,
            module.load_allowlist(ALLOWLIST_PATH),
            {"example-package@1.2.3": "MIT"},
        )


def test_complex_spdx_expression_is_parsed_conservatively() -> None:
    module = load_module()

    assert module.verify_license_allowlist(
        sbom("(MIT OR Apache-2.0) AND BSD-3-Clause"), module.load_allowlist(ALLOWLIST_PATH)
    ) == {"MIT", "Apache-2.0", "BSD-3-Clause"}


def test_permitted_or_branch_does_not_require_disallowed_alternative() -> None:
    module = load_module()

    assert module.verify_license_allowlist(sbom("MIT OR GPL-3.0-or-later"), module.load_allowlist(ALLOWLIST_PATH)) == {
        "MIT"
    }


def test_disallowed_and_term_still_fails() -> None:
    module = load_module()

    with pytest.raises(module.UnapprovedLicenseError, match="unapproved"):
        module.verify_license_allowlist(sbom("MIT AND GPL-3.0-or-later"), module.load_allowlist(ALLOWLIST_PATH))


def test_unknown_spdx_token_fails_closed() -> None:
    module = load_module()

    with pytest.raises(module.UnapprovedLicenseError, match="unapproved"):
        module.verify_license_allowlist(sbom("MIT OR LicenseRef-Proprietary"), module.load_allowlist(ALLOWLIST_PATH))


def test_component_id_fallback_is_accepted() -> None:
    module = load_module()
    document = {
        "bomFormat": "CycloneDX",
        "components": [
            {
                "name": "fixture-component",
                "version": "1.0.0",
                "licenses": [{"license": {"id": "Apache-2.0"}}],
            }
        ],
    }

    assert module.verify_license_allowlist(document, module.load_allowlist(ALLOWLIST_PATH)) == {"Apache-2.0"}


def test_direct_cyclonedx_expression_is_accepted() -> None:
    module = load_module()
    document = {
        "bomFormat": "CycloneDX",
        "components": [
            {
                "name": "fixture-component",
                "version": "1.0.0",
                "licenses": [{"expression": "Apache-2.0 AND MIT"}],
            }
        ],
    }

    assert module.verify_license_allowlist(document, module.load_allowlist(ALLOWLIST_PATH)) == {"Apache-2.0", "MIT"}


def test_write_notice_updates_only_generated_section(tmp_path: Path) -> None:
    module = load_module()
    notices = tmp_path / "THIRD_PARTY_NOTICES.md"
    notices.write_text(
        "# Third-Party Notices\n\nBefore\n\n"
        "<!-- GENERATED LICENSE SET START -->\nold\n<!-- GENERATED LICENSE SET END -->\n\n"
        "After\n",
        encoding="utf-8",
    )

    module.write_notice(
        notices,
        {
            "MIT",
            "Apache-2.0",
        },
        [
            {"name": "alpha", "version": "1.0.0", "licenses": ["MIT"]},
            {"name": "beta", "version": "2.0.0", "licenses": ["Apache-2.0"]},
        ],
    )

    text = notices.read_text(encoding="utf-8")
    assert "Before" in text
    assert "After" in text
    assert "alpha 1.0.0: MIT" in text
    assert "beta 2.0.0: Apache-2.0" in text


def test_write_notice_deduplicates_identical_components(tmp_path: Path) -> None:
    module = load_module()
    notices = tmp_path / "notices.md"
    record = {"name": "shared-package", "version": "1.0.0", "licenses": ["MIT"]}

    module.write_notice(notices, {"MIT"}, [record, dict(record)])

    assert notices.read_text(encoding="utf-8").count("- shared-package 1.0.0: MIT") == 1


def test_main_updates_notices_from_fixture_sboms(tmp_path: Path) -> None:
    module = load_module()
    sbom_dir = tmp_path / "sbom"
    sbom_dir.mkdir()
    notices = tmp_path / "THIRD_PARTY_NOTICES.md"
    allowlist = tmp_path / "license-allowlist.md"
    allowlist.write_text(ALLOWLIST_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    notices.write_text("# Third-Party Notices\n", encoding="utf-8")
    (sbom_dir / "fixture.cdx.json").write_text(
        json.dumps(
            {
                "bomFormat": "CycloneDX",
                "components": [
                    {
                        "name": "fixture-component",
                        "version": "1.0.0",
                        "licenses": [{"license": {"expression": "MIT"}}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    exit_code = module.main(
        [
            "--sbom-dir",
            str(sbom_dir),
            "--allowlist",
            str(allowlist),
            "--notices",
            str(notices),
        ]
    )

    assert exit_code == 0
    assert "fixture-component 1.0.0: MIT" in notices.read_text(encoding="utf-8")


def test_main_applies_reviewed_license_overrides(tmp_path: Path) -> None:
    module = load_module()
    sbom_dir = tmp_path / "sbom"
    sbom_dir.mkdir()
    notices = tmp_path / "THIRD_PARTY_NOTICES.md"
    overrides = tmp_path / "license-overrides.json"
    overrides.write_text(json.dumps({"fixture-component@1.0.0": "MIT"}), encoding="utf-8")
    (sbom_dir / "fixture.cdx.json").write_text(
        json.dumps(
            {
                "bomFormat": "CycloneDX",
                "components": [{"name": "fixture-component", "version": "1.0.0"}],
            }
        ),
        encoding="utf-8",
    )

    exit_code = module.main(
        [
            "--sbom-dir",
            str(sbom_dir),
            "--allowlist",
            str(ALLOWLIST_PATH),
            "--overrides",
            str(overrides),
            "--notices",
            str(notices),
        ]
    )

    assert exit_code == 0
    assert "fixture-component 1.0.0: MIT" in notices.read_text(encoding="utf-8")


def test_main_can_exclude_inventory_only_sbom_from_notices(tmp_path: Path) -> None:
    module = load_module()
    sbom_dir = tmp_path / "sbom"
    sbom_dir.mkdir()
    notices = tmp_path / "THIRD_PARTY_NOTICES.md"
    (sbom_dir / "application.cdx.json").write_text(json.dumps(sbom("MIT")), encoding="utf-8")
    (sbom_dir / "uv-lock.cdx.json").write_text(json.dumps(sbom(None)), encoding="utf-8")

    exit_code = module.main(
        [
            "--sbom-dir",
            str(sbom_dir),
            "--allowlist",
            str(ALLOWLIST_PATH),
            "--exclude-sbom",
            "uv-lock.cdx.json",
            "--notices",
            str(notices),
        ]
    )

    assert exit_code == 0
    assert "fixture-component 1.0.0: MIT" in notices.read_text(encoding="utf-8")
