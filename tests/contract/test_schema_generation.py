from __future__ import annotations

import hashlib
import subprocess
from collections.abc import Iterator
from pathlib import Path
from shutil import which

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = ROOT / "packages/contracts/schema"


@pytest.fixture(autouse=True)
def _restore_schema_dir() -> Iterator[None]:
    # These tests regenerate the tracked schema files as a side effect. `export-contracts.py`
    # emits raw json.dumps output that only becomes canonical after generate-contracts.mjs
    # reformats it, so snapshot and restore the committed files to keep the working tree clean.
    snapshot = {path: path.read_bytes() for path in SCHEMA_DIR.glob("*.json")}
    try:
        yield
    finally:
        for path in SCHEMA_DIR.glob("*.json"):
            if path not in snapshot:
                path.unlink()
        for path, data in snapshot.items():
            path.write_bytes(data)


def executable(name: str) -> str:
    path = which(name)
    if path is None:
        raise RuntimeError(f"required executable is unavailable: {name}")
    return path


def run_local(command: list[str], cwd: Path = ROOT) -> None:
    # Commands are fixed by this repository test and executable paths are resolved above.
    subprocess.run(command, cwd=cwd, check=True)  # noqa: S603


def digest_tree(path: Path) -> str:
    digest = hashlib.sha256()
    for file_path in sorted(path.glob("*.json")):
        digest.update(file_path.name.encode())
        digest.update(file_path.read_bytes())
    return digest.hexdigest()


def test_schema_export_is_deterministic() -> None:
    run_local([executable("uv"), "run", "python", "scripts/export-contracts.py"])
    first = digest_tree(SCHEMA_DIR)
    run_local([executable("uv"), "run", "python", "scripts/export-contracts.py"])
    assert digest_tree(SCHEMA_DIR) == first


def test_contract_generation_formats_exported_schemas() -> None:
    run_local([executable("uv"), "run", "python", "scripts/export-contracts.py"])
    run_local([executable("node"), "scripts/generate-contracts.mjs"])
    run_local(
        [executable("npx"), "prettier", "--check", "packages/contracts/schema"],
    )


def test_event_schema_contains_every_public_envelope_field() -> None:
    body = (SCHEMA_DIR / "activity-event.schema.json").read_text(encoding="utf-8")
    for field in ["eventId", "sequence", "sessionId", "taskId", "occurredAt", "type", "payload"]:
        assert f'"{field}"' in body


def test_activity_event_schema_compiles_with_strict_ajv_2020() -> None:
    run_local([executable("uv"), "run", "python", "scripts/export-contracts.py"])
    schema_path = SCHEMA_DIR / "activity-event.schema.json"
    schema_argument = repr(str(schema_path))
    run_local(
        [
            executable("node"),
            "--input-type=module",
            "--eval",
            (
                'import { readFileSync } from "node:fs"; '
                'import Ajv2020 from "ajv/dist/2020.js"; '
                'import addFormats from "ajv-formats"; '
                "const ajv = new Ajv2020({ strict: true, discriminator: true }); "
                "addFormats(ajv); ajv.compile("
                f'JSON.parse(readFileSync({schema_argument}, "utf8")));'
            ),
        ],
        cwd=ROOT / "packages/contracts/typescript",
    )
