from __future__ import annotations

import ast
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def imports_under(path: Path) -> set[str]:
    imported: set[str] = set()
    for source_path in path.rglob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
    return imported


def test_contracts_do_not_import_application_packages() -> None:
    imports = imports_under(ROOT / "packages/contracts/python/src")
    assert not any(name.startswith(("eda_api", "eda_worker", "eda_provenance")) for name in imports)


def test_provenance_does_not_import_application_packages() -> None:
    imports = imports_under(ROOT / "packages/provenance/src")
    assert not any(name.startswith(("eda_api", "eda_worker")) for name in imports)


def test_api_reaches_the_analyst_runtime_only_through_its_analysis_boundary() -> None:
    # The analyst runtime runs inside the API; only `eda_api.analysis` may import it, and never the sandbox.
    boundary = ROOT / "apps/api/src/eda_api/analysis"
    for source_path in (ROOT / "apps/api/src").rglob("*.py"):
        imports = imports_under_file(source_path)
        assert not any(name.startswith("eda_sandbox") for name in imports), source_path
        if boundary not in source_path.parents:
            assert not any(name.startswith("eda_worker") for name in imports), source_path


def imports_under_file(source_path: Path) -> set[str]:
    imported: set[str] = set()
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def test_shipped_runtimes_do_not_include_retired_durable_task_topology() -> None:
    for project in ("apps/api", "services/worker"):
        configuration = tomllib.loads((ROOT / project / "pyproject.toml").read_text(encoding="utf-8"))
        dependencies = configuration["project"]["dependencies"]
        assert not any(
            dependency.startswith(("durabletask", "agent-framework-durabletask")) for dependency in dependencies
        )

    retired_sources = (
        "apps/api/src/eda_api/durable.py",
        "services/worker/src/eda_worker/callbacks.py",
        "services/worker/src/eda_worker/host.py",
        "services/worker/src/eda_worker/orchestration.py",
        "services/worker/src/eda_worker/process.py",
    )
    assert not any((ROOT / relative).exists() for relative in retired_sources)
