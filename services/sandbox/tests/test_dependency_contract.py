import json
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
        "matplotlib": "3.11.1",
        "seaborn": "0.13.2",
        "plotly": "6.9.0",
        "kaleido": "1.3.0",
        "pillow": "12.3.0",
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
