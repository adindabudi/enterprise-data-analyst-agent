import hashlib
import json
import re
import tomllib
from importlib.metadata import version
from pathlib import Path


def test_analysis_runtime_versions_are_exact() -> None:
    expected = {
        "pandas": "3.0.5",
        "numpy": "2.5.1",
        "pyarrow": "25.0.0",
        "duckdb": "1.5.5",
        "scipy": "1.18.0",
        "statsmodels": "0.14.6",
        "scikit-learn": "1.9.0",
        "pandera": "0.32.1",
        "plotly": "6.9.0",
        "kaleido": "1.3.0",
        "pypng": "0.20220715.0",
        "pydyf": "0.12.1",
        "openpyxl": "3.1.5",
        "xlsxwriter": "3.2.9",
    }
    assert {name: version(name) for name in expected} == expected


def test_runtime_does_not_install_network_or_shell_helpers() -> None:
    prohibited = {"requests", "httpx", "aiohttp", "paramiko", "fabric"}
    manifest = tomllib.loads(Path("services/sandbox/pyproject.toml").read_text(encoding="utf-8"))
    dependencies = set(manifest["project"]["dependencies"])
    assert all(not any(dependency.startswith(name) for dependency in dependencies) for name in prohibited)


def test_baked_web_toolchain_includes_every_pinned_shadcn_runtime_dependency() -> None:
    manifest = json.loads(Path("services/sandbox/package.json").read_text(encoding="utf-8"))
    dependencies = set(manifest["dependencies"])
    assert dependencies.isdisjoint(
        {
            "@mermaid-js/mermaid-cli",
            "@parcel/config-default",
            "@rolldown/plugin-babel",
            "@vitejs/plugin-react",
            "autoprefixer",
            "babel-plugin-react-compiler",
            "parcel",
            "parcel-resolver-tspaths",
            "sharp",
        }
    )
    assert manifest["overrides"]["sax"] == "1.4.1"
    assert manifest["dependencies"]["mermaid"] == "11.17.2"
    assert manifest["dependencies"]["puppeteer"] == "25.3.0"
    assert manifest["dependencies"]["react-resizable-panels"] == "2.1.9"
    required = {
        "@radix-ui/react-accordion",
        "@radix-ui/react-aspect-ratio",
        "@radix-ui/react-avatar",
        "@radix-ui/react-checkbox",
        "@radix-ui/react-collapsible",
        "@radix-ui/react-context-menu",
        "@radix-ui/react-dialog",
        "@radix-ui/react-dropdown-menu",
        "@radix-ui/react-hover-card",
        "@radix-ui/react-label",
        "@radix-ui/react-menubar",
        "@radix-ui/react-navigation-menu",
        "@radix-ui/react-popover",
        "@radix-ui/react-progress",
        "@radix-ui/react-radio-group",
        "@radix-ui/react-scroll-area",
        "@radix-ui/react-select",
        "@radix-ui/react-separator",
        "@radix-ui/react-slider",
        "@radix-ui/react-slot",
        "@radix-ui/react-switch",
        "@radix-ui/react-tabs",
        "@radix-ui/react-toast",
        "@radix-ui/react-toggle",
        "@radix-ui/react-toggle-group",
        "@radix-ui/react-tooltip",
        "class-variance-authority",
        "clsx",
        "cmdk",
        "embla-carousel-react",
        "lucide-react",
        "next-themes",
        "react-day-picker",
        "react-hook-form",
        "react-resizable-panels",
        "sonner",
        "tailwind-merge",
        "vaul",
    }

    assert required - dependencies == set()


def test_pptx_distribution_preserves_upstream_files_without_unused_parser() -> None:
    root = Path("services/sandbox")
    manifest = json.loads((root / "package.json").read_text(encoding="utf-8"))
    assert manifest["dependencies"]["pptxgenjs"] == "file:vendor/pptxgenjs"
    vendor = root / "vendor/pptxgenjs"
    package = json.loads((vendor / "package.json").read_text(encoding="utf-8"))
    provenance = json.loads((vendor / "upstream.json").read_text(encoding="utf-8"))
    assert package["license"] == "MIT"
    assert "image-size" not in package["dependencies"]
    for filename, digest in provenance["files"].items():
        assert hashlib.sha256((vendor / filename).read_bytes()).hexdigest() == digest
    assert "LICENSE" in provenance["files"]
    lock = json.loads((root / "package-lock.json").read_text(encoding="utf-8"))
    assert not any(path.endswith("node_modules/image-size") for path in lock["packages"])
    assert "install-links=true" in (root / ".npmrc").read_text(encoding="utf-8")


def test_sandbox_python_lock_matches_workspace_versions() -> None:
    workspace = tomllib.loads(Path("uv.lock").read_text(encoding="utf-8"))
    versions = {package["name"]: package["version"] for package in workspace["package"]}
    requirements = Path("services/sandbox/requirements.lock").read_text(encoding="utf-8")
    for name, pinned in re.findall(r"^([A-Za-z0-9_.-]+)==([^\s;\\]+)", requirements, flags=re.MULTILINE):
        normalized = re.sub(r"[-_.]+", "-", name).lower()
        assert versions[normalized] == pinned, f"Sandbox lock drift: {name}=={pinned} != {versions[normalized]}"


def test_document_runtime_has_no_pillow_or_pdfium_dependency_chain() -> None:
    workspace = tomllib.loads(Path("uv.lock").read_text(encoding="utf-8"))
    names = {package["name"] for package in workspace["package"]}
    prohibited = {"pillow", "pypdfium2", "pdfplumber", "python-pptx", "reportlab", "matplotlib", "seaborn"}

    assert not names & prohibited
    assert {"plotly", "kaleido", "pypdf", "pypng", "pydyf"} <= names
    manifest = json.loads(Path("services/sandbox/package.json").read_text(encoding="utf-8"))
    assert manifest["dependencies"]["pdf-lib"] == "1.17.1"
    assert manifest["dependencies"]["pptx2json"] == "0.0.10"
