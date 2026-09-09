from __future__ import annotations

import html
import json
import subprocess
import tarfile
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from eda_artifacts.html import validate_web_artifact_html

MAX_WEB_ARTIFACT_BYTES = 10 * 1024 * 1024
MAX_APP_SOURCE_CHARS = 24_000
MAX_STYLES_CHARS = 16_000
CSP = (
    "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
    "img-src data: blob:; font-src data:; connect-src 'none'; object-src 'none'; "
    "base-uri 'none'; form-action 'none'"
)


@dataclass(frozen=True)
class WebArtifactToolchain:
    node: str = "/usr/local/bin/node"
    vite: str = "/opt/eda/node_modules/.bin/vite"
    node_modules: Path = Path("/opt/eda/node_modules")
    components_archive: Path = Path("/opt/web-skills/web-artifacts-builder/scripts/shadcn-components.tar.gz")
    inliner_script: Path = Path("/opt/eda/inline-web-artifact.mjs")


type CommandRunner = Callable[[Sequence[str], Path, Mapping[str, str]], None]


def run_command(command: Sequence[str], cwd: Path, environment: Mapping[str, str]) -> None:
    completed = subprocess.run(  # noqa: S603 - executables and arguments come from the fixed baked toolchain.
        list(command),
        cwd=cwd,
        env=dict(environment),
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        details = completed.stderr.strip() or completed.stdout.strip() or "web artifact build tool failed"
        if len(details) > 2_000:
            details = f"{details[:400]}\n...\n{details[-1_595:]}"
        raise RuntimeError(details)


def bundle_web_artifact(
    *,
    app_source: str,
    styles: str,
    title: str,
    output: Path,
    toolchain: WebArtifactToolchain | None = None,
    runner: CommandRunner = run_command,
) -> None:
    active_toolchain = toolchain or WebArtifactToolchain()
    _validate_inputs(app_source, styles, title, output, active_toolchain)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="eda-web-artifact-") as temporary_directory:
        project = Path(temporary_directory)
        _write_project(project, app_source=app_source, styles=styles, title=title)
        _extract_components(active_toolchain.components_archive, project / "src")
        (project / "node_modules").symlink_to(active_toolchain.node_modules, target_is_directory=True)
        environment = {
            "HOME": str(project),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "NODE_ENV": "production",
            "NODE_PATH": str(active_toolchain.node_modules),
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "TZ": "UTC",
        }
        runner(
            [
                active_toolchain.vite,
                "build",
                "--outDir",
                "dist",
            ],
            project,
            environment,
        )
        temporary_output = project / "bundle.html"
        runner(
            [
                active_toolchain.node,
                str(active_toolchain.inliner_script),
                str(project / "dist/index.html"),
                str(temporary_output),
            ],
            project,
            environment,
        )
        if not temporary_output.is_file() or temporary_output.stat().st_size > MAX_WEB_ARTIFACT_BYTES:
            raise ValueError("web artifact bundle is missing or exceeds the size limit")
        document = temporary_output.read_text(encoding="utf-8")
        validate_web_artifact_html(document)
        runner(
            [
                active_toolchain.node,
                str(active_toolchain.inliner_script.with_name("verify-web-artifact.mjs")),
                str(temporary_output),
            ],
            project,
            environment,
        )
        output.write_text(document, encoding="utf-8")


def _validate_inputs(
    app_source: str,
    styles: str,
    title: str,
    output: Path,
    toolchain: WebArtifactToolchain,
) -> None:
    if not app_source.strip() or len(app_source) > MAX_APP_SOURCE_CHARS:
        raise ValueError("web artifact app source is required")
    if len(styles) > MAX_STYLES_CHARS:
        raise ValueError("web artifact styles exceed the size limit")
    if not title.strip() or len(title) > 120:
        raise ValueError("web artifact title is invalid")
    if output.suffix.casefold() not in {".htm", ".html"}:
        raise ValueError("web artifact output must be HTML")
    if not toolchain.node_modules.is_dir():
        raise ValueError("baked web artifact node modules are unavailable")
    for candidate in (toolchain.components_archive, toolchain.inliner_script):
        if not candidate.is_file() or candidate.is_symlink():
            raise ValueError("baked web artifact toolchain is incomplete")
    for executable in (toolchain.node, toolchain.vite):
        if not Path(executable).is_absolute():
            raise ValueError("web artifact executables must be absolute paths")


def _write_project(project: Path, *, app_source: str, styles: str, title: str) -> None:
    source = project / "src"
    source.mkdir()
    (project / "package.json").write_text(
        json.dumps(
            {
                "name": "eda-web-artifact",
                "private": True,
                "type": "module",
                "version": "1.0.0",
                "alias": {"@/*": "./src/$1"},
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    (project / ".postcssrc").write_text(
        json.dumps({"plugins": {"tailwindcss": {}}}, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    (project / "vite.config.mjs").write_text(
        "import path from 'node:path';\n"
        "import { defineConfig } from 'vite';\n"
        "export default defineConfig({"
        "resolve:{alias:{'@':path.resolve(process.cwd(),'src')}},"
        "build:{minify:false,sourcemap:false}"
        "});\n",
        encoding="utf-8",
    )
    (project / "tailwind.config.cjs").write_text(
        "module.exports = {darkMode: ['class'], content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'], "
        "theme: {extend: {}}, plugins: []};\n",
        encoding="utf-8",
    )
    (project / "tsconfig.json").write_text(
        json.dumps(
            {
                "compilerOptions": {
                    "baseUrl": ".",
                    "jsx": "react-jsx",
                    "lib": ["ES2022", "DOM", "DOM.Iterable"],
                    "module": "ESNext",
                    "moduleResolution": "Bundler",
                    "paths": {"@/*": ["./src/*"]},
                    "strict": True,
                    "target": "ES2022",
                },
                "include": ["src"],
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    (project / "index.html").write_text(
        '<!doctype html><html><head><meta charset="UTF-8">'
        f'<meta http-equiv="Content-Security-Policy" content="{html.escape(CSP, quote=True)}">'
        '<meta name="viewport" content="width=device-width,initial-scale=1.0">'
        f'<title>{html.escape(title)}</title></head><body><div id="root"></div>'
        '<script type="module" src="./src/main.tsx"></script></body></html>',
        encoding="utf-8",
    )
    (source / "main.tsx").write_text(
        "import React from 'react';\n"
        "import { createRoot } from 'react-dom/client';\n"
        "import App from './App';\n"
        "import './index.css';\n"
        "createRoot(document.getElementById('root')!).render(<React.StrictMode><App /></React.StrictMode>);\n",
        encoding="utf-8",
    )
    (source / "App.tsx").write_text(app_source, encoding="utf-8")
    (source / "index.css").write_text(
        "@tailwind base;\n@tailwind components;\n@tailwind utilities;\n" + styles,
        encoding="utf-8",
    )


def _extract_components(archive_path: Path, destination: Path) -> None:
    with tarfile.open(archive_path, mode="r:gz") as archive:
        members = archive.getmembers()
        for member in members:
            relative = PurePosixPath(member.name)
            if (
                relative.is_absolute()
                or ".." in relative.parts
                or member.issym()
                or member.islnk()
                or not (member.isfile() or member.isdir())
            ):
                raise ValueError("unsafe component archive")
        archive.extractall(destination, members=members, filter="data")
