from __future__ import annotations

import asyncio
import hashlib
import json
import re
import secrets
import shlex
import time
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Protocol, cast

from azure.containerapps.sandbox import EgressPolicy

from eda_worker.sandbox_contract import EXECUTION_CONTEXT, INPUTS, OUTPUTS, SOURCES, WORKSPACE

from .client import (
    ExecutionRecord,
    FileRecord,
    Runtime,
    ValidationProfile,
    ValidationResult,
    ValidationStatus,
)

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024
MAX_OUTPUT_FILES = 1000
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$")
_FILE_ID = re.compile(
    r"^(?:file_|source-|input-|output-|stdout-|stderr-|validation-)[A-Za-z0-9_-]{8,}(?:\.[A-Za-z0-9]{1,10})?$"
)
_SANDBOX_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{7,127}$")


class SandboxExecResult(Protocol):
    exit_code: int
    stdout: str
    stderr: str


class SandboxFileInfo(Protocol):
    name: str
    path: str
    size: int | None
    is_directory: bool


class SandboxDirListing(Protocol):
    entries: list[SandboxFileInfo]


class SandboxClient(Protocol):
    sandbox_id: str

    def write_file(self, path: str, content: bytes, **kwargs: object) -> None: ...

    def read_file(self, path: str, **kwargs: object) -> bytes: ...

    def delete_file(self, path: str, **kwargs: object) -> None: ...

    def mkdir(self, path: str, **kwargs: object) -> None: ...

    def list_files(self, path: str, **kwargs: object) -> SandboxDirListing: ...

    def exec(self, command: str, **kwargs: object) -> SandboxExecResult: ...

    def delete(self) -> None: ...


class SandboxGroupClient(Protocol):
    def begin_create_sandbox(self, **kwargs: object) -> Any: ...

    def get_sandbox_client(self, sandbox_id: str) -> SandboxClient: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class _IndexedFile:
    record: FileRecord
    path: str


class AcaSandboxClient:
    def __init__(
        self,
        group_client: SandboxGroupClient,
        *,
        disk_image_id: str,
        cpu: str = "2000m",
        memory: str = "4096Mi",
        auto_suspend_seconds: int = 300,
        max_upload_bytes: int = MAX_UPLOAD_BYTES,
        max_download_bytes: int = MAX_DOWNLOAD_BYTES,
    ) -> None:
        if not disk_image_id:
            raise ValueError("sandbox disk image ID is required")
        self._group = group_client
        self._disk_image_id = disk_image_id
        self._cpu = cpu
        self._memory = memory
        self._auto_suspend_seconds = auto_suspend_seconds
        self._max_upload_bytes = max_upload_bytes
        self._max_download_bytes = max_download_bytes
        self._files: dict[tuple[str, str], _IndexedFile] = {}
        self._deleted: set[str] = set()

    def create_identifier(self) -> str:
        return f"sbx_{secrets.token_hex(16)}"

    async def create(self, task_id: str) -> str:
        if not re.fullmatch(r"task_[A-Za-z0-9_-]{8,}", task_id):
            raise ValueError("task ID is invalid")
        poller = await asyncio.to_thread(
            self._group.begin_create_sandbox,
            disk=None,
            disk_id=self._disk_image_id,
            cpu=self._cpu,
            memory=self._memory,
            auto_suspend_seconds=self._auto_suspend_seconds,
            auto_suspend_mode="Disk",
            labels={"taskId": task_id},
            egress_policy=EgressPolicy(default_action="Deny", traffic_inspection="Full"),
        )
        sandbox = await asyncio.to_thread(poller.result)
        sandbox_id = cast(str, sandbox.sandbox_id)
        self._validate_sandbox_id(sandbox_id)
        return sandbox_id

    async def allocate(self, task_id: str, proposed_identifier: str) -> str:
        del proposed_identifier
        return await self.create(task_id)

    async def import_bytes(
        self,
        sandbox_id: str,
        category: str,
        display_name: str,
        body: bytes,
    ) -> FileRecord:
        self._validate_sandbox_id(sandbox_id)
        if category not in {"source", "input"}:
            raise ValueError("file category is invalid")
        if not _SAFE_NAME.fullmatch(display_name):
            raise ValueError("display name is invalid")
        if len(body) > self._max_upload_bytes:
            raise ValueError("upload exceeds the sandbox limit")
        digest = hashlib.sha256(body).hexdigest()
        prefix = "source" if category == "source" else "input"
        suffix = PurePosixPath(display_name).suffix.lower()
        file_id = f"{prefix}-{digest[:32]}{suffix}"
        category_root = SOURCES if category == "source" else INPUTS
        path = f"{category_root}/{file_id}"
        sandbox = self._sandbox(sandbox_id)
        await asyncio.to_thread(sandbox.write_file, path, body, create_dirs=True)
        record = FileRecord(
            file_id=f"file_{digest[:32]}",
            category=category,
            display_name=display_name,
            size_bytes=len(body),
            sha256=digest,
        )
        indexed = _IndexedFile(record=record, path=path)
        self._files[(sandbox_id, record.file_id)] = indexed
        self._files[(sandbox_id, file_id)] = indexed
        return record.model_copy(update={"file_id": file_id})

    async def execute(
        self,
        sandbox_id: str,
        runtime: Runtime,
        source_file_id: str,
        timeout_seconds: int,
        parameters: dict[str, str | int | float | bool | None] | None = None,
    ) -> ExecutionRecord:
        source = self._file(sandbox_id, source_file_id)
        if source.record.category != "source":
            raise ValueError("source file ID is invalid")
        executable = {Runtime.PYTHON: "python", Runtime.JAVASCRIPT: "node"}[runtime]
        command = f"{executable} {shlex.quote(source.path)}"
        if parameters:
            command = f"{command} --params {shlex.quote(json.dumps(parameters, sort_keys=True, separators=(',', ':')))}"
        sandbox = self._sandbox(sandbox_id)
        await self._reset_output_directory(sandbox_id, sandbox)
        inputs = {
            indexed.path: {
                "display_name": indexed.record.display_name,
                "path": indexed.path,
                "sha256": indexed.record.sha256,
            }
            for (owner, _), indexed in self._files.items()
            if owner == sandbox_id and indexed.record.category == "input"
        }
        context = {
            "inputs": [inputs[path] for path in sorted(inputs)],
            "output_directory": OUTPUTS,
            "parameters": parameters or {},
        }
        await asyncio.to_thread(
            sandbox.write_file,
            EXECUTION_CONTEXT,
            json.dumps(context, sort_keys=True).encode(),
            create_dirs=True,
        )
        started = time.monotonic()
        result = await asyncio.wait_for(
            asyncio.to_thread(sandbox.exec, command, working_directory=WORKSPACE),
            timeout=timeout_seconds,
        )
        duration_ms = int((time.monotonic() - started) * 1000)
        execution_id = f"exec_{secrets.token_hex(16)}"
        output_file_ids = await self._collect_outputs(sandbox_id, sandbox)
        stdout_id = await self._index_output(sandbox_id, "stdout", result.stdout.encode(), f"{execution_id}.stdout")
        stderr_id = await self._index_output(sandbox_id, "stderr", result.stderr.encode(), f"{execution_id}.stderr")
        return ExecutionRecord(
            execution_id=execution_id,
            status="succeeded" if result.exit_code == 0 else "failed",
            runtime=runtime,
            source_file_id=source_file_id,
            stdout_file_id=stdout_id,
            stderr_file_id=stderr_id,
            output_file_ids=output_file_ids,
            return_code=result.exit_code,
            duration_ms=duration_ms,
            peak_rss_bytes=0,
            cpu_time_ms=0,
        )

    async def validate(
        self,
        sandbox_id: str,
        file_id: str,
        profile: ValidationProfile,
    ) -> ValidationResult:
        candidate = self._file(sandbox_id, file_id)
        command = self.validation_command(file_id, profile)
        sandbox = self._sandbox(sandbox_id)
        result = await asyncio.to_thread(sandbox.exec, command)
        try:
            report_payload = await asyncio.to_thread(sandbox.read_file, f"{OUTPUTS}/validation.json")
        except (KeyError, OSError):
            report_payload = result.stdout.encode() if result.stdout else result.stderr.encode()
        if not report_payload:
            report_payload = json.dumps(
                {"status": "passed" if result.exit_code == 0 else "failed", "profile": profile.value}
            ).encode()
        report_id = await self._index_output(
            sandbox_id,
            "validation",
            report_payload,
            f"{file_id}.validation.json",
        )
        report = self._file(sandbox_id, report_id).record
        return ValidationResult(
            validation_id=f"validation_{secrets.token_hex(16)}",
            file_id=candidate.record.file_id,
            profile=profile,
            status=ValidationStatus.PASSED if result.exit_code == 0 else ValidationStatus.FAILED,
            report=report,
        )

    def validation_command(self, file_id: str, profile: ValidationProfile) -> str:
        self._validate_file_id(file_id)
        return (
            "python -m eda_sandbox.cli validate "
            f"--file {INPUTS}/{file_id} --profile {profile.value} "
            f"--output {OUTPUTS}/validation.json"
        )

    async def describe_file(self, sandbox_id: str, file_id: str) -> FileRecord:
        return self._file(sandbox_id, file_id).record

    async def download_file(self, sandbox_id: str, file_id: str) -> bytes:
        indexed = self._file(sandbox_id, file_id)
        content = await asyncio.to_thread(self._sandbox(sandbox_id).read_file, indexed.path)
        if len(content) > self._max_download_bytes:
            raise ValueError("download exceeds the sandbox limit")
        return content

    async def delete(self, sandbox_id: str) -> None:
        self._validate_sandbox_id(sandbox_id)
        if sandbox_id in self._deleted:
            return
        await asyncio.to_thread(self._sandbox(sandbox_id).delete)
        self._deleted.add(sandbox_id)
        for key in [key for key in self._files if key[0] == sandbox_id]:
            self._files.pop(key, None)

    async def stop(self, sandbox_id: str) -> None:
        await self.delete(sandbox_id)

    async def close(self) -> None:
        await asyncio.to_thread(self._group.close)

    async def _index_output(self, sandbox_id: str, category: str, content: bytes, display_name: str) -> str:
        digest = hashlib.sha256(content).hexdigest()
        file_id = f"{category}-{digest[:32]}"
        path = f"{OUTPUTS}/{file_id}"
        await asyncio.to_thread(self._sandbox(sandbox_id).write_file, path, content, create_dirs=True)
        record = FileRecord(
            file_id=f"file_{digest[:32]}",
            category=category,
            display_name=display_name,
            size_bytes=len(content),
            sha256=digest,
        ).model_copy(update={"file_id": file_id})
        self._files[(sandbox_id, file_id)] = _IndexedFile(record=record, path=path)
        return file_id

    async def _reset_output_directory(self, sandbox_id: str, sandbox: SandboxClient) -> None:
        result = await asyncio.to_thread(
            sandbox.exec,
            f"rm -rf -- {OUTPUTS} && mkdir -p -- {OUTPUTS}",
            working_directory=WORKSPACE,
        )
        if result.exit_code != 0:
            raise RuntimeError("sandbox output directory could not be reset")
        for key, indexed in list(self._files.items()):
            if key[0] == sandbox_id and indexed.path.startswith(f"{OUTPUTS}/"):
                self._files.pop(key, None)

    async def _collect_outputs(self, sandbox_id: str, sandbox: SandboxClient) -> tuple[str, ...]:
        listing = await asyncio.to_thread(sandbox.list_files, OUTPUTS)
        if len(listing.entries) > MAX_OUTPUT_FILES:
            raise ValueError("sandbox produced too many output files")
        output_ids: list[str] = []
        total_bytes = 0
        for entry in sorted(listing.entries, key=lambda value: value.name):
            if entry.is_directory or not _SAFE_NAME.fullmatch(entry.name):
                raise ValueError("sandbox output is unsafe")
            expected_path = f"{OUTPUTS}/{entry.name}"
            if entry.path != expected_path:
                raise ValueError("sandbox output path is unsafe")
            content = await asyncio.to_thread(sandbox.read_file, entry.path)
            total_bytes += len(content)
            if total_bytes > self._max_download_bytes:
                raise ValueError("sandbox outputs exceed the download limit")
            digest = hashlib.sha256(content).hexdigest()
            suffix = PurePosixPath(entry.name).suffix.lower()
            file_id = f"output-{digest[:32]}{suffix}"
            record = FileRecord(
                file_id=f"file_{digest[:32]}",
                category="output",
                display_name=entry.name,
                size_bytes=len(content),
                sha256=digest,
            ).model_copy(update={"file_id": file_id})
            self._files[(sandbox_id, file_id)] = _IndexedFile(record=record, path=entry.path)
            output_ids.append(file_id)
        return tuple(output_ids)

    def _file(self, sandbox_id: str, file_id: str) -> _IndexedFile:
        self._validate_sandbox_id(sandbox_id)
        self._validate_file_id(file_id)
        indexed = self._files.get((sandbox_id, file_id))
        if indexed is None:
            raise ValueError("file ID is unavailable")
        return indexed

    def _sandbox(self, sandbox_id: str) -> SandboxClient:
        self._validate_sandbox_id(sandbox_id)
        return self._group.get_sandbox_client(sandbox_id)

    @staticmethod
    def _validate_sandbox_id(sandbox_id: str) -> None:
        if not _SANDBOX_ID.fullmatch(sandbox_id):
            raise ValueError("sandbox ID is invalid")

    @staticmethod
    def _validate_file_id(file_id: str) -> None:
        if not _FILE_ID.fullmatch(file_id) or PurePosixPath(file_id).name != file_id:
            raise ValueError("file ID is invalid")
