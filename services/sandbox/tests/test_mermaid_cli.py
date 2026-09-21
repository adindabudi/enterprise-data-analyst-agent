from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from shutil import which

import png
import pytest
from defusedxml import ElementTree
from eda_artifacts.images import validate_png, validate_svg
from eda_artifacts.pdf import render_page_with_poppler
from pypdf import PdfReader

NODE = which("node") or "/usr/bin/node"
ROOT = Path(__file__).resolve().parents[3]


def test_local_mermaid_cli_exposes_supported_options() -> None:
    script = Path("services/sandbox/scripts/render-mermaid.mjs")

    result = subprocess.run(  # noqa: S603 - fixed Node executable and repository-owned script.
        [NODE, str(script), "--help"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "-i, --input" in result.stdout
    assert "-o, --output" in result.stdout
    assert "-p, --puppeteerConfigFile" in result.stdout
    assert "-c, --configFile" in result.stdout


def test_local_mermaid_cli_forces_strict_security_after_config_merge() -> None:
    source = Path("services/sandbox/scripts/render-mermaid.mjs").read_text(encoding="utf-8")

    config_merge = source.index("...config")
    strict_mode = source.index('securityLevel: "strict"', config_merge)
    render_call = source.index("globalThis.mermaid.render", config_merge)

    assert config_merge < strict_mode < render_call


@pytest.fixture
def browser_config(tmp_path: Path) -> Path:
    browser = os.environ.get("PUPPETEER_EXECUTABLE_PATH") or which("chromium")
    if browser is None or not Path(browser).is_file():
        pytest.skip("Chromium or PUPPETEER_EXECUTABLE_PATH is required for real Mermaid rendering")
    if not (ROOT / "services/sandbox/node_modules/puppeteer").is_dir():
        pytest.skip("install the sandbox npm dependencies for real Mermaid rendering")
    configuration = tmp_path / "browser.json"
    configuration.write_text(
        json.dumps({"executablePath": browser, "args": ["--no-sandbox", "--disable-dev-shm-usage"]}),
        encoding="utf-8",
    )
    return configuration


def render_diagram(tmp_path: Path, browser_config: Path, *, kind: str, background: str, source: str) -> Path:
    definition = tmp_path / "diagram.mmd"
    definition.write_text(source, encoding="utf-8")
    configuration = tmp_path / "mermaid.json"
    configuration.write_text(json.dumps({"htmlLabels": False, "securityLevel": "loose"}), encoding="utf-8")
    output = tmp_path / f"diagram.{kind}"
    result = subprocess.run(  # noqa: S603
        [
            NODE,
            str(ROOT / "services/sandbox/scripts/render-mermaid.mjs"),
            "-p",
            str(browser_config),
            "-c",
            str(configuration),
            "-i",
            str(definition),
            "-o",
            str(output),
            "-b",
            background,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=40,
    )
    assert result.returncode == 0, result.stderr
    return output


@pytest.mark.parametrize("background", ["red", "transparent"])
@pytest.mark.parametrize("kind", ["svg", "png", "pdf"])
def test_mermaid_background_and_rendered_output(
    tmp_path: Path, browser_config: Path, kind: str, background: str
) -> None:
    output = render_diagram(
        tmp_path,
        browser_config,
        kind=kind,
        background=background,
        source="flowchart LR\n  Input[Input] --> Output[Verified output]\n",
    )
    if kind == "svg":
        document = ElementTree.fromstring(output.read_bytes())
        assert "Verified output" in " ".join(" ".join(document.itertext()).split())
        assert f"background-color: {background}" in document.attrib["style"]
    elif kind == "png":
        width, height, rows, _ = png.Reader(filename=str(output)).asRGBA8()
        pixels = list(rows)
        assert width > 50 and height > 20
        assert tuple(pixels[0][:4]) == ((255, 0, 0, 255) if background == "red" else (0, 0, 0, 0))
        assert any(
            tuple(row[offset : offset + 4]) != tuple(pixels[0][:4])
            for row in pixels
            for offset in range(0, len(row), 4)
        )
    else:
        document = PdfReader(output)
        assert len(document.pages) == 1
        assert "Verified output" in document.pages[0].extract_text()
        _, _, rows, _ = png.Reader(bytes=render_page_with_poppler(output.read_bytes(), 0)).asRGBA8()
        assert tuple(next(rows)[:4]) == ((255, 0, 0, 255) if background == "red" else (255, 255, 255, 255))


@pytest.mark.parametrize(
    "source,background,error",
    [
        ("not a diagram", "white", "No diagram type detected"),
        ("flowchart LR; Input-->Output", "url(https://example.test/image)", "valid CSS color"),
    ],
)
def test_invalid_mermaid_input_does_not_publish_output(
    tmp_path: Path, browser_config: Path, source: str, background: str, error: str
) -> None:
    definition = tmp_path / "invalid.mmd"
    definition.write_text(source, encoding="utf-8")
    output = tmp_path / "invalid.svg"
    result = subprocess.run(  # noqa: S603
        [
            NODE,
            str(ROOT / "services/sandbox/scripts/render-mermaid.mjs"),
            "-p",
            str(browser_config),
            "-i",
            str(definition),
            "-o",
            str(output),
            "-b",
            background,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=40,
    )

    assert result.returncode != 0
    assert error in result.stderr
    assert not output.exists()


def test_plotly_chart_renders_without_pillow(
    tmp_path: Path, browser_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import plotly.graph_objects as graph_objects

    browser = json.loads(browser_config.read_text(encoding="utf-8"))["executablePath"]
    monkeypatch.setenv("BROWSER_PATH", browser)
    figure = graph_objects.Figure(graph_objects.Bar(x=["First", "Second"], y=[3, 7]))
    figure.update_layout(width=480, height=320, title="Verified totals")
    bitmap = tmp_path / "chart.png"
    vector = tmp_path / "chart.svg"
    figure.write_image(bitmap)
    figure.write_image(vector)

    assert validate_png(bitmap.read_bytes()) == (480, 320)
    validate_svg(vector.read_text(encoding="utf-8"))
    assert "Verified totals" in vector.read_text(encoding="utf-8")
