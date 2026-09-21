from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

import pytest
from eda_contracts import ArtifactKind, ArtifactRef
from eda_worker.documents.provenance import document_bundle_scope, is_trusted_document_bundle
from eda_worker.documents.runner import DocumentSkillScriptRunner, DocumentTaskScopeMiddleware
from eda_worker.sandbox.gateway import (
    CosmosBlobArtifactGatewayStore,
    DynamicSessionCapabilityGateway,
    InMemoryArtifactGatewayStore,
)
from eda_worker.tools.contracts import CapabilityResult, CapabilityStatus, ExecuteSandboxOperation, SandboxRuntime

from services.worker.tests.sandbox.test_gateway import FakeClient, FakeRuntimeRepository, _task
from services.worker.tests.sandbox.test_gateway_artifact_store import FakeBlobContainer, FakeWorkspaceContainer


@dataclass(frozen=True)
class Skill:
    path: str


@dataclass(frozen=True)
class Script:
    full_path: str
    name: str = "scripts/validate.py"


class FakeGateway:
    def __init__(self, *, cancelled: bool = False) -> None:
        self.cancelled = cancelled
        self.execute_calls: list[tuple[str, ExecuteSandboxOperation]] = []

    async def is_cancelled(self, task_id: str) -> bool:
        assert task_id == "task_01HZZZZZZZZZZZZZZZZZZZZZZZ"
        return self.cancelled

    async def input_filename(self, task_id: str, artifact: ArtifactRef) -> str:
        del task_id
        return f"{artifact.artifact_id}.bin"

    async def execute(self, task_id: str, operation: ExecuteSandboxOperation) -> CapabilityResult:
        self.execute_calls.append((task_id, operation))
        return CapabilityResult(status=CapabilityStatus.OK, summary="Skill script completed.")


def artifact(identifier: str, kind: ArtifactKind) -> ArtifactRef:
    return ArtifactRef(artifact_id=identifier, version=1, kind=kind, sha256="a" * 64)


@pytest.mark.asyncio
async def test_runner_executes_the_verified_bundle_in_the_existing_sandbox(tmp_path: Path) -> None:
    bundle_root = tmp_path / "document-skills"
    (bundle_root / "pptx" / "scripts").mkdir(parents=True)
    script_path = bundle_root / "pptx" / "scripts" / "validate.py"
    script_path.write_text("print('validate')\n")
    gateway = FakeGateway()
    progress: list[tuple[str, str, str | None, str]] = []

    async def report(task_id: str, milestone: str, detail: str | None, state: str) -> None:
        progress.append((task_id, milestone, detail, state))

    runner = DocumentSkillScriptRunner(
        gateway=gateway,
        task_id="task_01HZZZZZZZZZZZZZZZZZZZZZZZ",
        bundle_root=bundle_root,
        bundle_artifacts={"pptx": artifact("input-pptx-bundle", ArtifactKind.SCRIPT)},
        progress=report,
    )

    result = await runner(
        Skill(path=str(bundle_root / "pptx")),
        Script(full_path=str(script_path)),
        {"document": artifact("input-presentation", ArtifactKind.PPTX).model_dump(mode="json", by_alias=True)},
    )

    assert result["status"] == "ok"
    _, operation = gateway.execute_calls[-1]
    assert operation.runtime.value == "python"
    assert {reference.artifact_id for reference in operation.input_artifacts} == {
        "input-pptx-bundle",
        "input-presentation",
    }
    assert "import zipfile" in operation.source
    assert "runpy.run_path" in operation.source
    assert progress == [
        (
            "task_01HZZZZZZZZZZZZZZZZZZZZZZZ",
            "Running pptx document skill",
            "Verified skill script started.",
            "running",
        ),
        (
            "task_01HZZZZZZZZZZZZZZZZZZZZZZZ",
            "Pptx document skill finished",
            "Skill script completed.",
            "completed",
        ),
    ]


@pytest.mark.asyncio
async def test_runner_rejects_a_script_outside_its_resolved_skill(tmp_path: Path) -> None:
    bundle_root = tmp_path / "document-skills"
    (bundle_root / "pdf").mkdir(parents=True)
    gateway = FakeGateway()
    runner = DocumentSkillScriptRunner(
        gateway=gateway,
        task_id="task_01HZZZZZZZZZZZZZZZZZZZZZZZ",
        bundle_root=bundle_root,
        bundle_artifacts={"pdf": artifact("input-pdf-bundle", ArtifactKind.SCRIPT)},
    )

    with pytest.raises(ValueError, match="does not belong to"):
        await runner(Skill(path=str(bundle_root / "pdf")), Script(full_path="/etc/passwd"), None)

    assert gateway.execute_calls == []


@pytest.mark.asyncio
async def test_runner_checks_cancellation_before_sandbox_execution(tmp_path: Path) -> None:
    bundle_root = tmp_path / "document-skills"
    (bundle_root / "docx" / "scripts").mkdir(parents=True)
    script_path = bundle_root / "docx" / "scripts" / "validate.py"
    script_path.write_text("print('validate')\n")
    gateway = FakeGateway(cancelled=True)
    runner = DocumentSkillScriptRunner(
        gateway=gateway,
        task_id="task_01HZZZZZZZZZZZZZZZZZZZZZZZ",
        bundle_root=bundle_root,
        bundle_artifacts={"docx": artifact("input-docx-bundle", ArtifactKind.SCRIPT)},
    )

    result = await runner(Skill(path=str(bundle_root / "docx")), Script(full_path=str(script_path)), None)

    assert result == {"status": "cancelled", "summary": "Task cancellation was requested."}
    assert gateway.execute_calls == []


@pytest.mark.asyncio
async def test_middleware_supplies_trusted_task_scope_and_loads_bundle_per_task(tmp_path: Path) -> None:
    bundle_root = tmp_path / "document-skills"
    (bundle_root / "pdf" / "scripts").mkdir(parents=True)
    script_path = bundle_root / "pdf" / "scripts" / "validate.py"
    script_path.write_text("print('validate')\n")
    gateway = FakeGateway()
    loaded: list[tuple[str, str]] = []

    async def bundle_loader(task_id: str, skill_name: str) -> ArtifactRef:
        loaded.append((task_id, skill_name))
        return artifact("input-pdf-bundle", ArtifactKind.SCRIPT)

    runner = DocumentSkillScriptRunner(
        gateway=gateway,
        bundle_root=bundle_root,
        bundle_loader=bundle_loader,
    )

    class Context:
        kwargs: ClassVar[dict[str, object]] = {"task_id": "task_01HZZZZZZZZZZZZZZZZZZZZZZZ"}

    result: dict[str, object] | None = None

    async def call_next() -> None:
        nonlocal result
        result = await runner(Skill(path=str(bundle_root / "pdf")), Script(full_path=str(script_path)), None)

    await DocumentTaskScopeMiddleware().process(Context(), call_next)  # type: ignore[arg-type]

    assert result is not None and result["status"] == "ok"
    assert loaded == [("task_01HZZZZZZZZZZZZZZZZZZZZZZZ", "pdf")]


@pytest.mark.parametrize("backend", ["memory", "cosmos"])
@pytest.mark.parametrize("unbound_document", [False, True])
@pytest.mark.asyncio
async def test_runner_authorizes_only_its_trusted_bundle_for_one_invocation(
    tmp_path: Path, backend: str, unbound_document: bool
) -> None:
    bundle_root = tmp_path / "document-skills"
    script_path = bundle_root / "pdf" / "scripts" / "validate.py"
    script_path.parent.mkdir(parents=True)
    script_path.write_text("print('verified script')\n")
    runtime = FakeRuntimeRepository(_task())
    store = (
        CosmosBlobArtifactGatewayStore(runtime, FakeWorkspaceContainer(), FakeBlobContainer())
        if backend == "cosmos"
        else InMemoryArtifactGatewayStore()
    )
    client = FakeClient()
    gateway = DynamicSessionCapabilityGateway(client, runtime, store)
    bundle = await store.persist_bytes(runtime.task.id, ArtifactKind.INPUT, "document-skill-pdf.zip", b"trusted-bundle")
    lookalike = await store.persist_bytes(
        runtime.task.id, ArtifactKind.INPUT, "document-skill-pdf.zip", b"untrusted-data"
    )
    operation = ExecuteSandboxOperation(runtime=SandboxRuntime.PYTHON, source="print(1)", input_artifacts=(bundle,))
    assert (await gateway.execute(runtime.task.id, operation)).status is CapabilityStatus.BLOCKED

    async def load_bundle(task_id: str, skill_name: str) -> ArtifactRef:
        assert task_id == runtime.task.id and skill_name == "pdf"
        return bundle

    runner = DocumentSkillScriptRunner(
        gateway=gateway, task_id=runtime.task.id, bundle_root=bundle_root, bundle_loader=load_bundle
    )
    args = {"document": lookalike.model_dump(mode="json", by_alias=True)} if unbound_document else None
    result = await runner(Skill(path=str(bundle_root / "pdf")), Script(full_path=str(script_path)), args)

    assert result["status"] == ("blocked" if unbound_document else "ok")
    assert client.execution_calls == (0 if unbound_document else 1)
    assert runtime.task.input_artifacts == ()
    assert (await gateway.execute(runtime.task.id, operation)).status is CapabilityStatus.BLOCKED
    if backend == "cosmos":
        with pytest.raises(ValueError, match="input artifact is unavailable"):
            await store.read_bytes(runtime.task.id, bundle)


@pytest.mark.parametrize("backend", ["memory", "cosmos"])
@pytest.mark.parametrize(
    ("kind", "display_name"),
    [(ArtifactKind.INPUT, "sales.csv"), (ArtifactKind.INPUT, "report.PDF"), (ArtifactKind.PPTX, "generated.pptx")],
)
@pytest.mark.asyncio
async def test_document_bootstrap_uses_the_names_imported_by_the_gateway(
    tmp_path: Path, backend: str, kind: ArtifactKind, display_name: str
) -> None:
    bundle_root = tmp_path / "document-skills"
    script_path = bundle_root / "pdf" / "scripts" / "validate.py"
    script_path.parent.mkdir(parents=True)
    script_path.write_text("print('verified script')\n")
    runtime = FakeRuntimeRepository(_task())
    store = (
        CosmosBlobArtifactGatewayStore(runtime, FakeWorkspaceContainer(), FakeBlobContainer())
        if backend == "cosmos"
        else InMemoryArtifactGatewayStore()
    )
    client = FakeClient()
    gateway = DynamicSessionCapabilityGateway(client, runtime, store)
    bundle = await store.persist_bytes(runtime.task.id, ArtifactKind.INPUT, "document-skill-pdf.zip", b"trusted-bundle")
    document = await store.persist_bytes(runtime.task.id, kind, display_name, b"document-bytes")
    if kind is ArtifactKind.INPUT:
        runtime.task = runtime.task.model_copy(update={"input_artifacts": (document,)})
    runner = DocumentSkillScriptRunner(
        gateway=gateway, task_id=runtime.task.id, bundle_root=bundle_root, bundle_artifacts={"pdf": bundle}
    )

    result = await runner(
        Skill(path=str(bundle_root / "pdf")),
        Script(full_path=str(script_path)),
        {"document": document.model_dump(mode="json", by_alias=True)},
    )

    assert result["status"] == "ok"
    source = next(body.decode() for category, _, body in client.imported if category == "source")
    imported_names = [name for category, name, _ in client.imported if category == "input"]
    expected_document_suffix = Path(display_name).suffix.lower()
    assert imported_names == [f"{bundle.artifact_id}.zip", f"{document.artifact_id}{expected_document_suffix}"]
    for name in imported_names:
        assert repr(name) in source


def test_document_bundle_provenance_is_exact_and_cleared_on_failure() -> None:
    task_id = "task_provenance123"
    bundle = artifact("input-trusted-pdf-bundle", ArtifactKind.INPUT)
    assert not is_trusted_document_bundle(task_id, bundle)

    with pytest.raises(RuntimeError, match="sandbox failed"), document_bundle_scope(task_id, bundle):
        assert is_trusted_document_bundle(task_id, bundle)
        assert not is_trusted_document_bundle("task_other12345", bundle)
        for updates in (
            {"artifact_id": "input-another-bundle"},
            {"version": 2},
            {"kind": ArtifactKind.DATA},
            {"sha256": "b" * 64},
        ):
            assert not is_trusted_document_bundle(task_id, bundle.model_copy(update=updates))
        raise RuntimeError("sandbox failed")

    assert not is_trusted_document_bundle(task_id, bundle)
