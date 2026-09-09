from __future__ import annotations

import json
from dataclasses import dataclass

import pytest
from azure.containerapps.sandbox import EgressPolicy
from eda_worker.sandbox.aca_client import AcaSandboxClient
from eda_worker.sandbox.client import Runtime, ValidationProfile


@dataclass
class FakeExecResult:
    stdout: str
    stderr: str
    exit_code: int


@dataclass
class FakeFileInfo:
    name: str
    path: str
    size: int
    is_directory: bool = False


@dataclass
class FakeDirListing:
    path: str
    entries: list[FakeFileInfo]


class FakeSandbox:
    def __init__(self, sandbox_id: str) -> None:
        self.sandbox_id = sandbox_id
        self.files: dict[str, bytes] = {}
        self.commands: list[str] = []
        self.working_directories: list[str | None] = []
        self.deleted = 0
        self.file_api_output_directory_is_unwritable = False
        self.output_directory_writable = True
        self.reset_fails = False

    def write_file(self, path: str, content: bytes, **kwargs: object) -> None:
        del kwargs
        self.files[path] = content

    def read_file(self, path: str, **kwargs: object) -> bytes:
        del kwargs
        return self.files[path]

    def exec(self, command: str, **kwargs: object) -> FakeExecResult:
        self.commands.append(command)
        self.working_directories.append(kwargs.get("working_directory") if kwargs else None)
        if command == "rm -rf -- /workspace/task/outputs && mkdir -p -- /workspace/task/outputs":
            if self.reset_fails:
                return FakeExecResult(stdout="", stderr="reset failed", exit_code=1)
            for candidate in list(self.files):
                if candidate.startswith("/workspace/task/outputs/"):
                    del self.files[candidate]
            self.output_directory_writable = True
        if command.startswith("python "):
            if not self.output_directory_writable:
                return FakeExecResult(stdout="", stderr="PermissionError: [Errno 13] Permission denied", exit_code=1)
            self.files["/workspace/task/outputs/report.html"] = b"<!doctype html><button>Ready</button>"
        return FakeExecResult(stdout="ok\n", stderr="", exit_code=0)

    def delete_file(self, path: str, **kwargs: object) -> None:
        del kwargs
        for candidate in list(self.files):
            if candidate == path or candidate.startswith(f"{path.rstrip('/')}/"):
                del self.files[candidate]

    def mkdir(self, path: str, **kwargs: object) -> None:
        del kwargs
        if path == "/workspace/task/outputs" and self.file_api_output_directory_is_unwritable:
            self.output_directory_writable = False

    def list_files(self, path: str, **kwargs: object) -> FakeDirListing:
        del kwargs
        prefix = f"{path.rstrip('/')}/"
        entries = [
            FakeFileInfo(name=candidate.removeprefix(prefix), path=candidate, size=len(content))
            for candidate, content in self.files.items()
            if candidate.startswith(prefix) and "/" not in candidate.removeprefix(prefix)
        ]
        return FakeDirListing(path=path, entries=entries)

    def delete(self) -> None:
        self.deleted += 1


class FakePoller:
    def __init__(self, sandbox: FakeSandbox) -> None:
        self._sandbox = sandbox

    def result(self) -> FakeSandbox:
        return self._sandbox


class FakeGroupClient:
    def __init__(self) -> None:
        self.created: list[dict[str, object]] = []
        self.sandboxes: dict[str, FakeSandbox] = {}
        self.closed = 0

    def begin_create_sandbox(self, **kwargs: object) -> FakePoller:
        self.created.append(kwargs)
        sandbox = FakeSandbox("sbx_12345678")
        self.sandboxes[sandbox.sandbox_id] = sandbox
        return FakePoller(sandbox)

    def get_sandbox_client(self, sandbox_id: str) -> FakeSandbox:
        return self.sandboxes[sandbox_id]

    def close(self) -> None:
        self.closed += 1


@pytest.mark.asyncio
async def test_create_uses_the_pinned_disk_image_and_default_deny_egress() -> None:
    group = FakeGroupClient()
    client = AcaSandboxClient(group, disk_image_id="disk_12345678")

    sandbox_id = await client.create("task_12345678")

    assert sandbox_id == "sbx_12345678"
    assert len(group.created) == 1
    request = group.created[0]
    assert request["disk"] is None
    assert request["disk_id"] == "disk_12345678"
    assert request["cpu"] == "2000m"
    assert request["memory"] == "4096Mi"
    assert request["auto_suspend_seconds"] == 300
    assert request["auto_suspend_mode"] == "Disk"
    assert request["labels"] == {"taskId": "task_12345678"}
    policy = request["egress_policy"]
    assert isinstance(policy, EgressPolicy)
    assert policy.default_action == "Deny"
    assert policy.traffic_inspection == "Full"


@pytest.mark.asyncio
async def test_files_and_execution_use_only_opaque_ids_and_allowlisted_runtimes() -> None:
    group = FakeGroupClient()
    client = AcaSandboxClient(group, disk_image_id="disk_12345678")
    sandbox_id = await client.create("task_12345678")

    source = await client.import_bytes(sandbox_id, "source", "analysis.py", b"print('ok')")
    input_file = await client.import_bytes(sandbox_id, "input", "rows.json", b"[]")
    execution = await client.execute(sandbox_id, Runtime.PYTHON, source.file_id, 30, parameters={"limit": 5})

    assert source.file_id.startswith("source-")
    assert input_file.file_id.startswith("input-")
    assert source.file_id.endswith(".py")
    assert input_file.file_id.endswith(".json")
    assert execution.status == "succeeded"
    assert execution.stdout_file_id is not None
    assert len(execution.output_file_ids) == 1
    command = group.sandboxes[sandbox_id].commands[1]
    assert command.startswith("python /workspace/task/sources/")
    assert "--params" in command
    assert group.sandboxes[sandbox_id].working_directories == ["/workspace/task", "/workspace/task"]
    output = await client.describe_file(sandbox_id, execution.output_file_ids[0])
    assert output.display_name == "report.html"
    assert await client.download_file(sandbox_id, output.file_id) == b"<!doctype html><button>Ready</button>"
    assert await client.download_file(sandbox_id, execution.stdout_file_id) == b"ok\n"


@pytest.mark.asyncio
async def test_execution_accepts_an_existing_empty_output_directory() -> None:
    group = FakeGroupClient()
    client = AcaSandboxClient(group, disk_image_id="disk_12345678")
    sandbox_id = await client.create("task_12345678")
    source = await client.import_bytes(sandbox_id, "source", "analysis.py", b"print('ok')")

    execution = await client.execute(sandbox_id, Runtime.PYTHON, source.file_id, 30)

    assert execution.status == "succeeded"


@pytest.mark.asyncio
async def test_execution_writes_context_with_real_imported_paths() -> None:
    group = FakeGroupClient()
    client = AcaSandboxClient(group, disk_image_id="disk_12345678")
    sandbox_id = await client.create("task_12345678")
    source = await client.import_bytes(sandbox_id, "source", "analysis.py", b"print('ok')")
    imported = await client.import_bytes(sandbox_id, "input", "revenue.csv", b"quarter,revenue\nQ1,120")

    await client.execute(sandbox_id, Runtime.PYTHON, source.file_id, 30, parameters={"currency": "IDR"})

    context = json.loads(group.sandboxes[sandbox_id].files["/workspace/task/execution-context.json"])
    assert context["output_directory"] == "/workspace/task/outputs"
    assert context["parameters"] == {"currency": "IDR"}
    assert context["inputs"] == [{
        "display_name": "revenue.csv",
        "path": f"/workspace/task/inputs/{imported.file_id}",
        "sha256": imported.sha256,
    }]


@pytest.mark.asyncio
async def test_execution_resets_outputs_with_runtime_user_permissions() -> None:
    group = FakeGroupClient()
    client = AcaSandboxClient(group, disk_image_id="disk_12345678")
    sandbox_id = await client.create("task_12345678")
    sandbox = group.sandboxes[sandbox_id]
    sandbox.file_api_output_directory_is_unwritable = True
    source = await client.import_bytes(sandbox_id, "source", "analysis.py", b"print('ok')")

    execution = await client.execute(sandbox_id, Runtime.PYTHON, source.file_id, 30)

    assert execution.status == "succeeded"
    assert len(execution.output_file_ids) == 1
    assert sandbox.commands[0] == "rm -rf -- /workspace/task/outputs && mkdir -p -- /workspace/task/outputs"
    assert sandbox.commands[1].startswith("python /workspace/task/sources/")


@pytest.mark.asyncio
async def test_execution_clears_a_stale_output_directory() -> None:
    group = FakeGroupClient()
    client = AcaSandboxClient(group, disk_image_id="disk_12345678")
    sandbox_id = await client.create("task_12345678")
    sandbox = group.sandboxes[sandbox_id]
    sandbox.files["/workspace/task/outputs/stale.html"] = b"stale"
    source = await client.import_bytes(sandbox_id, "source", "analysis.py", b"print('ok')")

    execution = await client.execute(sandbox_id, Runtime.PYTHON, source.file_id, 30)

    assert execution.status == "succeeded"
    assert "/workspace/task/outputs/stale.html" not in sandbox.files


@pytest.mark.asyncio
async def test_execution_rejects_a_failed_runtime_output_reset() -> None:
    group = FakeGroupClient()
    client = AcaSandboxClient(group, disk_image_id="disk_12345678")
    sandbox_id = await client.create("task_12345678")
    sandbox = group.sandboxes[sandbox_id]
    sandbox.reset_fails = True
    source = await client.import_bytes(sandbox_id, "source", "analysis.py", b"print('ok')")

    with pytest.raises(RuntimeError, match="output directory could not be reset"):
        await client.execute(sandbox_id, Runtime.PYTHON, source.file_id, 30)

    assert sandbox.commands == ["rm -rf -- /workspace/task/outputs && mkdir -p -- /workspace/task/outputs"]


@pytest.mark.asyncio
async def test_unknown_file_id_and_path_traversal_are_rejected() -> None:
    group = FakeGroupClient()
    client = AcaSandboxClient(group, disk_image_id="disk_12345678")
    sandbox_id = await client.create("task_12345678")

    with pytest.raises(ValueError, match="display name"):
        await client.import_bytes(sandbox_id, "input", "../escape", b"bad")
    with pytest.raises(ValueError, match="file ID"):
        await client.download_file(sandbox_id, "missing-file")


@pytest.mark.asyncio
async def test_delete_is_idempotent_and_reconstructs_the_sandbox_client() -> None:
    group = FakeGroupClient()
    client = AcaSandboxClient(group, disk_image_id="disk_12345678")
    sandbox_id = await client.create("task_12345678")

    await client.delete(sandbox_id)
    await client.delete(sandbox_id)
    await client.close()

    assert group.sandboxes[sandbox_id].deleted == 1
    assert group.closed == 1


def test_validation_uses_the_baked_sandbox_validator_contract() -> None:
    group = FakeGroupClient()
    client = AcaSandboxClient(group, disk_image_id="disk_12345678")

    command = client.validation_command("input-12345678.html", ValidationProfile.CORE_HTML)

    assert command == (
        "python -m eda_sandbox.cli validate --file /workspace/task/inputs/input-12345678.html "
        "--profile core_html --output /workspace/task/outputs/validation.json"
    )
