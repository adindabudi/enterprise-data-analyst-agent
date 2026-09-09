from __future__ import annotations

import os
import resource
import signal
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from .contracts import ExecutionRecord, ExecutionRequest, ExecutionStatus, Runtime
from .files import FileIndex
from .settings import (
    MAX_EXECUTION_SECONDS,
    MAX_OPEN_FILES,
    MAX_PROCESSES,
    MAX_STDIO_BYTES,
    WORKSPACE,
    execution_environment,
)

PRLIMIT = "/usr/bin/prlimit"
PYTHON = "/usr/local/bin/python"
NODE = "/usr/local/bin/node"


class ExecutionManager:
    def __init__(self, path_resolver: Callable[[str], Path], *, file_index: FileIndex | None = None) -> None:
        self._path_resolver = path_resolver
        self._file_index = file_index
        self.recorded_argv: list[str] = []

    def run(self, request: ExecutionRequest) -> ExecutionRecord:
        source = self._path_resolver(request.source_file_id)
        argv = self._argv(request.runtime, source)
        self.recorded_argv = argv
        started = time.monotonic()
        usage_before = resource.getrusage(resource.RUSAGE_CHILDREN)
        process = subprocess.Popen(  # noqa: S603 -- argv contains fixed launcher/runtime paths and indexed source path only.
            argv,
            cwd=WORKSPACE if WORKSPACE.exists() else source.parent,
            env=execution_environment(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        try:
            stdout, stderr = process.communicate(timeout=min(request.timeout_seconds, MAX_EXECUTION_SECONDS))
            status = ExecutionStatus.SUCCEEDED if process.returncode == 0 else ExecutionStatus.FAILED
        except subprocess.TimeoutExpired:
            self._terminate_group(process)
            stdout, stderr = process.communicate()
            status = ExecutionStatus.TIMED_OUT
        finally:
            self._terminate_group(process)
        usage_after = resource.getrusage(resource.RUSAGE_CHILDREN)
        stdout = stdout[:MAX_STDIO_BYTES]
        stderr = stderr[:MAX_STDIO_BYTES]
        duration_ms = int((time.monotonic() - started) * 1000)
        stdout_file_id: str | None = None
        stderr_file_id: str | None = None
        output_file_ids: tuple[str, ...] = ()
        if self._file_index is not None:
            outputs = self._file_index.scan_outputs()
            output_file_ids = tuple(record.file_id for record in outputs)
            if stdout:
                stdout_file_id = self._file_index.import_bytes("stdio", "stdout.txt", stdout).file_id
            if stderr:
                stderr_file_id = self._file_index.import_bytes("stdio", "stderr.txt", stderr).file_id
        return ExecutionRecord(
            execution_id=f"exec_{request.source_file_id}",
            status=status,
            runtime=request.runtime,
            source_file_id=request.source_file_id,
            stdout_file_id=stdout_file_id,
            stderr_file_id=stderr_file_id,
            output_file_ids=output_file_ids,
            return_code=process.returncode,
            duration_ms=duration_ms,
            peak_rss_bytes=int(usage_after.ru_maxrss * 1024),
            cpu_time_ms=max(
                0,
                int(
                    (usage_after.ru_utime + usage_after.ru_stime - usage_before.ru_utime - usage_before.ru_stime) * 1000
                ),
            ),
        )

    @staticmethod
    def _argv(runtime: Runtime, source: Path) -> list[str]:
        runtime_argv = {
            Runtime.PYTHON: [PYTHON, "-I", "-B", str(source)],
            Runtime.JAVASCRIPT: [NODE, "--disable-proto=throw", str(source)],
        }[runtime]
        return [
            PRLIMIT,
            f"--nproc={MAX_PROCESSES}",
            f"--nofile={MAX_OPEN_FILES}",
            "--fsize=524288000",
            "--core=0",
            "--",
            *runtime_argv,
        ]

    @staticmethod
    def _terminate_group(process: subprocess.Popen[bytes]) -> None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=2)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
