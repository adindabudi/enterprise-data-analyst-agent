from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import tarfile
from collections.abc import Mapping, Sequence
from html.parser import HTMLParser
from pathlib import Path

import pytest
from eda_artifacts.html import validate_web_artifact_html
from eda_sandbox.web_artifact import CommandRunner, WebArtifactToolchain, bundle_web_artifact, run_command


def components_archive(path: Path, *, unsafe: bool = False) -> Path:
    with tarfile.open(path, "w:gz") as archive:
        content = b"export const Button = () => null;\n"
        member = tarfile.TarInfo("../escape.tsx" if unsafe else "components/ui/button.tsx")
        member.size = len(content)
        archive.addfile(member, io.BytesIO(content))
    return path


class ScriptTags(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.attributes: list[dict[str, str | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script":
            self.attributes.append(dict(attrs))


@pytest.fixture
def browser_runner() -> CommandRunner:
    browser = os.environ.get("PUPPETEER_EXECUTABLE_PATH") or shutil.which("chromium")
    if browser is None or not Path(browser).is_file():
        pytest.skip("Chromium or PUPPETEER_EXECUTABLE_PATH is required for real render validation")

    def runner(command: Sequence[str], cwd: Path, environment: Mapping[str, str]) -> None:
        run_command(command, cwd, {**environment, "PUPPETEER_EXECUTABLE_PATH": browser})

    return runner


def test_bundler_uses_only_baked_tools_and_emits_validated_single_file_html(tmp_path: Path) -> None:
    node_modules = tmp_path / "node_modules"
    node_modules.mkdir()
    toolchain = WebArtifactToolchain(
        node="/usr/local/bin/node",
        vite="/opt/eda/node_modules/.bin/vite",
        node_modules=node_modules,
        components_archive=components_archive(tmp_path / "components.tar.gz"),
        inliner_script=tmp_path / "inline-web-artifact.mjs",
    )
    toolchain.inliner_script.write_text("// fixture\n", encoding="utf-8")
    calls: list[tuple[tuple[str, ...], Path, dict[str, str]]] = []
    package_private: list[bool] = []

    def runner(command: Sequence[str], cwd: Path, environment: Mapping[str, str]) -> None:
        calls.append((tuple(command), cwd, dict(environment)))
        package = json.loads((cwd / "package.json").read_text(encoding="utf-8"))
        package_private.append(package["private"])
        assert package["alias"] == {"@/*": "./src/$1"}
        assert not (cwd / ".parcelrc").exists()
        assert json.loads((cwd / ".postcssrc").read_text(encoding="utf-8")) == {"plugins": {"tailwindcss": {}}}
        assert (cwd / "node_modules").is_symlink()
        assert (cwd / "src/components/ui/button.tsx").is_file()
        assert "Quarterly revenue" in (cwd / "src/App.tsx").read_text(encoding="utf-8")
        if command[0] == toolchain.vite:
            (cwd / "dist").mkdir()
            (cwd / "dist/index.html").write_text(
                '<link rel="stylesheet" href="app.css"><div id="root"></div><script src="app.js"></script>',
                encoding="utf-8",
            )
            (cwd / "dist/app.css").write_text("body{color:#111}", encoding="utf-8")
            (cwd / "dist/app.js").write_text("document.body.dataset.ready='true'", encoding="utf-8")
            return
        if Path(command[1]).name == "verify-web-artifact.mjs":
            assert Path(command[-1]).is_file()
            return
        Path(command[-1]).write_text(
            '<meta http-equiv="Content-Security-Policy" '
            "content=\"default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
            "img-src data: blob:; font-src data:; connect-src 'none'; object-src 'none'; "
            "base-uri 'none'; form-action 'none'\">"
            "<style>body{color:#111}</style><div id='root'></div>"
            "<script>document.body.dataset.ready='true'</script>",
            encoding="utf-8",
        )

    output = tmp_path / "dashboard.html"
    bundle_web_artifact(
        app_source="export default function App(){return <main>Quarterly revenue</main>}",
        styles="main { display: grid; }",
        title="Executive dashboard",
        output=output,
        toolchain=toolchain,
        runner=runner,
    )

    validate_web_artifact_html(output.read_text(encoding="utf-8"))
    assert len(calls) == 3
    assert calls[0][0] == ("/opt/eda/node_modules/.bin/vite", "build", "--outDir", "dist")
    assert calls[1][0][:2] == ("/usr/local/bin/node", str(toolchain.inliner_script))
    assert calls[2][0][:2] == (
        "/usr/local/bin/node",
        str(toolchain.inliner_script.with_name("verify-web-artifact.mjs")),
    )
    assert all(command[0] not in {"npm", "npx", "pnpm"} for command, _, _ in calls)
    assert all(call_environment["NODE_PATH"] == str(node_modules) for _, _, call_environment in calls)
    assert package_private == [True, True, True]


@pytest.mark.integration
def test_real_bundle_preserves_module_bootstrap(tmp_path: Path, browser_runner: CommandRunner) -> None:
    sandbox_root = Path(__file__).resolve().parents[1]
    node_modules = sandbox_root / "node_modules"
    node = shutil.which("node")
    if node is None or not (node_modules / ".bin/vite").is_file():
        pytest.skip("Node and npm ci --prefix services/sandbox are required for the real bundler")
    toolchain = WebArtifactToolchain(
        node=node,
        vite=str(node_modules / ".bin/vite"),
        node_modules=node_modules,
        components_archive=components_archive(tmp_path / "components.tar.gz"),
        inliner_script=sandbox_root / "scripts/inline-web-artifact.mjs",
    )
    output = tmp_path / "dashboard.html"

    bundle_web_artifact(
        app_source=(
            "import {useState} from 'react';"
            "export default function App(){const [count,setCount]=useState(0);"
            "return <main><h1>Occupancy export</h1>"
            "<button onClick={()=>setCount(count+1)}>Count {count}</button></main>}"
        ),
        styles="main { padding: 24px; }",
        title="Occupancy export",
        output=output,
        toolchain=toolchain,
        runner=browser_runner,
    )

    scripts = ScriptTags()
    scripts.feed(output.read_text(encoding="utf-8"))
    assert scripts.attributes
    assert all(script.get("type") == "module" and "src" not in script for script in scripts.attributes)


@pytest.mark.integration
@pytest.mark.parametrize(
    ("app_source", "styles", "failure"),
    [
        (
            "const {useState}=React;"
            "export default function App(){const [count]=useState(0);"
            "return <React.Fragment><h1>Occupancy export</h1><p>{count}</p></React.Fragment>}",
            "",
            "React is not defined",
        ),
        (
            "export default function App(){throw new Error('component render failed')}",
            "",
            "component render failed",
        ),
        ("export default function App(){return null}", "", "rendered no visible content"),
        (
            "export default function App(){return <main>Dashboard</main>}",
            "body { display: none }",
            "rendered no visible content",
        ),
        (
            "export default function App(){return <main>Dashboard<img src='https://example.invalid/image.png'/></main>}",
            "",
            "violated its content security policy",
        ),
    ],
    ids=["unbound-react", "render-error", "empty-root", "hidden-body", "csp-blocked-resource"],
)
def test_real_bundle_rejects_render_failures_before_publishing(
    tmp_path: Path,
    browser_runner: CommandRunner,
    app_source: str,
    styles: str,
    failure: str,
) -> None:
    sandbox_root = Path(__file__).resolve().parents[1]
    node_modules = sandbox_root / "node_modules"
    node = shutil.which("node")
    if node is None or not (node_modules / ".bin/vite").is_file():
        pytest.skip("Node and sandbox npm dependencies are required for the real bundler")
    toolchain = WebArtifactToolchain(
        node=node,
        vite=str(node_modules / ".bin/vite"),
        node_modules=node_modules,
        components_archive=components_archive(tmp_path / "components.tar.gz"),
        inliner_script=sandbox_root / "scripts/inline-web-artifact.mjs",
    )
    output = tmp_path / "dashboard.html"

    with pytest.raises(RuntimeError, match=failure):
        bundle_web_artifact(
            app_source=app_source,
            styles=styles,
            title="Occupancy export",
            output=output,
            toolchain=toolchain,
            runner=browser_runner,
        )

    assert not output.exists()


@pytest.mark.integration
@pytest.mark.parametrize("resource", ["https://example.invalid/image.png", "private-image.png"])
def test_real_render_verifier_blocks_non_embedded_resources(
    tmp_path: Path,
    browser_runner: CommandRunner,
    resource: str,
) -> None:
    sandbox_root = Path(__file__).resolve().parents[1]
    node = shutil.which("node")
    if node is None or not (sandbox_root / "node_modules/puppeteer").is_dir():
        pytest.skip("Node and sandbox npm dependencies are required for render validation")
    document = tmp_path / "dashboard.html"
    document.write_text(
        f'<!doctype html><html><body><main id="root">Dashboard<img src="{resource}"></main></body></html>',
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="requested a non-embedded resource"):
        browser_runner(
            [node, str(sandbox_root / "scripts/verify-web-artifact.mjs"), str(document)],
            tmp_path,
            {"HOME": str(tmp_path), "PATH": "/usr/local/bin:/usr/bin:/bin"},
        )


@pytest.mark.integration
def test_real_inliner_preserves_mixed_script_types_and_raw_content(tmp_path: Path) -> None:
    sandbox_root = Path(__file__).resolve().parents[1]
    node = shutil.which("node")
    if node is None or not (sandbox_root / "node_modules/web-resource-inliner").is_dir():
        pytest.skip("Node and npm ci --prefix services/sandbox are required for the real inliner")
    input_path = tmp_path / "index.html"
    output_path = tmp_path / "bundle.html"
    classic_script = "window.caption = 'A & B < 5';"
    module_script = "document.getElementById('root').textContent = window.caption;"
    json_script = '{"caption":"A & B < 5"}'
    input_path.write_text(
        '<!doctype html><html><head><script src="classic.js"></script>'
        '<script type="module" crossorigin src="module.js"></script>'
        f'<script type="application/json" id="data">{json_script}</script>'
        '</head><body><div id="root"></div></body></html>',
        encoding="utf-8",
    )
    (tmp_path / "classic.js").write_text(classic_script, encoding="utf-8")
    (tmp_path / "module.js").write_text(module_script, encoding="utf-8")

    result = subprocess.run(  # noqa: S603 - fixed local inliner and disposable test paths.
        [node, str(sandbox_root / "scripts/inline-web-artifact.mjs"), str(input_path), str(output_path)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    document = output_path.read_text(encoding="utf-8")
    scripts = ScriptTags()
    scripts.feed(document)
    assert [script.get("type") for script in scripts.attributes] == [None, "module", "application/json"]
    assert all("src" not in script for script in scripts.attributes)
    assert "crossorigin" in scripts.attributes[1]
    assert scripts.attributes[2]["id"] == "data"
    assert all(content in document for content in (classic_script, module_script, json_script))


@pytest.mark.integration
def test_real_inliner_rejects_remote_resources(tmp_path: Path) -> None:
    sandbox_root = Path(__file__).resolve().parents[1]
    node = shutil.which("node")
    if node is None or not (sandbox_root / "node_modules/web-resource-inliner").is_dir():
        pytest.skip("Node and npm ci --prefix services/sandbox are required for the real inliner")
    input_path = tmp_path / "index.html"
    output_path = tmp_path / "bundle.html"
    input_path.write_text(
        '<script type="module" src="https://example.invalid/app.js"></script>',
        encoding="utf-8",
    )

    result = subprocess.run(  # noqa: S603 - fixed local inliner and disposable test paths.
        [node, str(sandbox_root / "scripts/inline-web-artifact.mjs"), str(input_path), str(output_path)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "remote resources are forbidden" in result.stderr
    assert not output_path.exists()


def test_bundler_rejects_component_archive_path_traversal(tmp_path: Path) -> None:
    node_modules = tmp_path / "node_modules"
    node_modules.mkdir()
    inliner = tmp_path / "inline-web-artifact.mjs"
    inliner.write_text("// fixture\n", encoding="utf-8")
    toolchain = WebArtifactToolchain(
        node="/usr/local/bin/node",
        vite="/opt/eda/node_modules/.bin/vite",
        node_modules=node_modules,
        components_archive=components_archive(tmp_path / "unsafe.tar.gz", unsafe=True),
        inliner_script=inliner,
    )

    with pytest.raises(ValueError, match="unsafe component archive"):
        bundle_web_artifact(
            app_source="export default function App(){return null}",
            styles="",
            title="Dashboard",
            output=tmp_path / "dashboard.html",
            toolchain=toolchain,
            runner=lambda *_: None,
        )

    assert not (tmp_path / "escape.tsx").exists()


def test_bundler_rejects_oversized_styles_before_running_tools(tmp_path: Path) -> None:
    node_modules = tmp_path / "node_modules"
    node_modules.mkdir()
    inliner = tmp_path / "inline-web-artifact.mjs"
    inliner.write_text("// fixture\n", encoding="utf-8")
    toolchain = WebArtifactToolchain(
        node="/usr/local/bin/node",
        vite="/opt/eda/node_modules/.bin/vite",
        node_modules=node_modules,
        components_archive=components_archive(tmp_path / "components.tar.gz"),
        inliner_script=inliner,
    )
    calls: list[Sequence[str]] = []

    with pytest.raises(ValueError, match="styles exceed"):
        bundle_web_artifact(
            app_source="export default function App(){return null}",
            styles="x" * 16_001,
            title="Dashboard",
            output=tmp_path / "dashboard.html",
            toolchain=toolchain,
            runner=lambda command, *_: calls.append(command),
        )

    assert calls == []


def test_build_failure_reports_bounded_tool_diagnostics(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def failed_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        del args, kwargs
        return subprocess.CompletedProcess(["vite"], 1, stdout="", stderr="vite failed: " + ("x" * 5_000))

    monkeypatch.setattr(subprocess, "run", failed_run)

    with pytest.raises(RuntimeError, match="vite failed") as captured:
        run_command(["/opt/eda/node_modules/.bin/vite", "build"], tmp_path, {})

    assert len(str(captured.value)) <= 2_100
