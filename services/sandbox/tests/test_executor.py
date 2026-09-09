from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from eda_sandbox import executor as executor_module
from eda_sandbox.contracts import ExecutionRequest
from eda_sandbox.executor import ExecutionManager
from eda_sandbox.files import FileIndex


def test_runtime_maps_to_fixed_argv(tmp_path) -> None:
    source = tmp_path / "source.py"
    source.write_text("print('safe')", encoding="utf-8")
    executor = ExecutionManager(path_resolver=lambda file_id: source)

    executor.run(ExecutionRequest(runtime="python", source_file_id="file_12345678"))

    assert executor.recorded_argv == [
        "/usr/bin/prlimit",
        "--nproc=128",
        "--nofile=1024",
        "--fsize=524288000",
        "--core=0",
        "--",
        "/usr/local/bin/python",
        "-I",
        "-B",
        str(source),
    ]


def test_javascript_maps_to_fixed_argv(tmp_path) -> None:
    source = tmp_path / "source.js"
    source.write_text("console.log('safe')", encoding="utf-8")
    executor = ExecutionManager(path_resolver=lambda file_id: source)

    executor.run(ExecutionRequest(runtime="javascript", source_file_id="file_12345678"))

    assert executor.recorded_argv[-3:] == ["/usr/local/bin/node", "--disable-proto=throw", str(source)]


def test_timeout_marks_execution_timed_out(tmp_path, monkeypatch) -> None:
    source = tmp_path / "timeout.py"
    source.write_text("import time\ntime.sleep(2)\n", encoding="utf-8")
    executor = ExecutionManager(path_resolver=lambda file_id: source)

    class FakeProcess:
        pid = 12345
        returncode = -15
        calls = 0

        def communicate(self, timeout=None):
            self.calls += 1
            if self.calls == 1:
                raise subprocess.TimeoutExpired("python", timeout)
            return b"", b""

    process = FakeProcess()
    monkeypatch.setattr(executor_module.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(ExecutionManager, "_terminate_group", staticmethod(lambda value: None))

    result = executor.run(ExecutionRequest(runtime="python", source_file_id="file_12345678", timeout_seconds=1))

    assert result.status == "timed_out"


def test_execution_indexes_bounded_stdio_and_outputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = tmp_path / "workspace"
    monkeypatch.setattr("eda_sandbox.files.IMPORTS", workspace / "inputs")
    monkeypatch.setattr("eda_sandbox.files.SOURCES", workspace / "sources")
    monkeypatch.setattr("eda_sandbox.files.OUTPUTS", workspace / "outputs")
    monkeypatch.setattr(executor_module, "WORKSPACE", workspace)
    monkeypatch.setattr(executor_module, "PYTHON", sys.executable)
    index = FileIndex()
    source = index.import_bytes(
        "source",
        "analysis.py",
        b"from pathlib import Path\nPath('outputs/result.txt').write_text('ready')\nprint('completed')\n",
    )
    manager = ExecutionManager(index.path_for, file_index=index)

    result = manager.run(ExecutionRequest(runtime="python", source_file_id=source.file_id))

    assert result.status == "succeeded"
    assert result.stdout_file_id is not None
    assert index.path_for(result.stdout_file_id).read_text(encoding="utf-8") == "completed\n"
    assert len(result.output_file_ids) == 1
    assert index.path_for(result.output_file_ids[0]).read_text(encoding="utf-8") == "ready"
    assert result.peak_rss_bytes > 0
    assert result.cpu_time_ms >= 0
