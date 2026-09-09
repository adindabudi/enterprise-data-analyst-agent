from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Protocol, cast

from agent_framework import FunctionInvocationContext, FunctionMiddleware
from eda_contracts import ArtifactRef, ProgressState

from eda_worker.tools.contracts import (
    CapabilityResult,
    CapabilityStatus,
    ExecuteSandboxOperation,
    SandboxRuntime,
    progress_state_for,
)

from .provenance import document_bundle_scope


class SkillSandboxGateway(Protocol):
    async def is_cancelled(self, task_id: str) -> bool: ...

    async def input_filename(self, task_id: str, artifact: ArtifactRef) -> str: ...

    async def execute(self, task_id: str, operation: ExecuteSandboxOperation) -> CapabilityResult: ...


type BundleLoader = Callable[[str, str], Awaitable[ArtifactRef]]
type ProgressReporter = Callable[[str, str, str | None, ProgressState], Awaitable[None]]
_CURRENT_TASK_ID: ContextVar[str | None] = ContextVar("document_skill_task_id", default=None)


class DocumentTaskScopeMiddleware(FunctionMiddleware):
    async def process(
        self,
        context: FunctionInvocationContext,
        call_next: Callable[[], Awaitable[None]],
    ) -> None:
        task_id = cast(object, context.kwargs.get("task_id"))
        if not isinstance(task_id, str) or not task_id.startswith("task_"):
            await call_next()
            return
        token = _CURRENT_TASK_ID.set(task_id)
        try:
            await call_next()
        finally:
            _CURRENT_TASK_ID.reset(token)


class DocumentSkillScriptRunner:
    def __init__(
        self,
        *,
        gateway: SkillSandboxGateway,
        task_id: str | None = None,
        bundle_root: Path,
        bundle_artifacts: Mapping[str, ArtifactRef] | None = None,
        bundle_loader: BundleLoader | None = None,
        progress: ProgressReporter | None = None,
    ) -> None:
        if task_id is None and bundle_loader is None:
            raise ValueError("document skill runner requires a trusted task scope and bundle source")
        self._gateway = gateway
        self._task_id = task_id
        self._bundle_root = bundle_root.resolve()
        self._bundle_artifacts = dict(bundle_artifacts or {})
        self._bundle_loader = bundle_loader
        self._progress = progress

    async def __call__(
        self, skill: Any, script: Any, args: dict[str, Any] | list[str] | None = None
    ) -> dict[str, object]:
        skill_name, skill_path = self._resolve_skill(skill)
        relative_script = self._resolve_script(skill_path, script)
        task_id = self._task_id or _CURRENT_TASK_ID.get()
        if task_id is None:
            raise ValueError("document skill invocation is missing its trusted task scope")
        if await self._gateway.is_cancelled(task_id):
            return {"status": "cancelled", "summary": "Task cancellation was requested."}
        bundle = self._bundle_artifacts.get(skill_name)
        if bundle is None and self._bundle_loader is not None:
            bundle = await self._bundle_loader(task_id, skill_name)
        if bundle is None:
            raise ValueError(f"document skill bundle is unavailable: {skill_name}")
        document = self._document_artifact(args)
        with document_bundle_scope(task_id, bundle):
            try:
                bundle_name = await self._gateway.input_filename(task_id, bundle)
                document_name = await self._gateway.input_filename(task_id, document) if document is not None else None
            except ValueError:
                return CapabilityResult(
                    status=CapabilityStatus.BLOCKED,
                    summary="Input artifact is unavailable in task scope.",
                    error_code="unbound_input_artifact",
                ).model_dump(mode="json", by_alias=True)
            operation = ExecuteSandboxOperation(
                runtime=SandboxRuntime.PYTHON,
                source=bootstrap_source(relative_script, args, bundle_name=bundle_name, document_name=document_name),
                input_artifacts=(bundle, document) if document is not None else (bundle,),
            )
            if self._progress is not None:
                await self._progress(
                    task_id, f"Running {skill_name} document skill", "Verified skill script started.", "running"
                )
            result = await self._gateway.execute(task_id, operation)
        if self._progress is not None:
            detail = result.summary if len(result.summary) <= 240 else f"{result.summary[:237]}..."
            await self._progress(
                task_id,
                f"{skill_name.capitalize()} document skill finished",
                detail,
                progress_state_for(result.status),
            )
        return result.model_dump(mode="json", by_alias=True)

    def _resolve_skill(self, skill: Any) -> tuple[str, Path]:
        raw_path = getattr(skill, "path", None)
        if not isinstance(raw_path, str):
            raise ValueError("document skill path is missing")
        skill_path = Path(raw_path).resolve()
        try:
            relative = skill_path.relative_to(self._bundle_root)
        except ValueError as error:
            raise ValueError("document skill does not belong to the bundle root") from error
        if len(relative.parts) != 1 or skill_path.is_symlink():
            raise ValueError("document skill does not belong to the bundle root")
        return relative.name, skill_path

    @staticmethod
    def _resolve_script(skill_path: Path, script: Any) -> str:
        raw_path = getattr(script, "full_path", None)
        if not isinstance(raw_path, str):
            raise ValueError("document skill script path is missing")
        script_path = Path(raw_path).resolve()
        try:
            return script_path.relative_to(skill_path).as_posix()
        except ValueError as error:
            raise ValueError("document skill script does not belong to its resolved skill") from error

    @staticmethod
    def _document_artifact(args: dict[str, Any] | list[str] | None) -> ArtifactRef | None:
        if args is None:
            return None
        if not isinstance(args, dict):
            raise ValueError("document skill script arguments must be an object")
        document = args.get("document")
        if document is None:
            return None
        if not isinstance(document, Mapping):
            raise ValueError("document skill document must be an artifact reference")
        return ArtifactRef.model_validate(document)


def bootstrap_source(
    relative_script: str,
    args: dict[str, Any] | list[str] | None,
    *,
    bundle_name: str,
    document_name: str | None,
) -> str:
    if not relative_script or relative_script.startswith("/") or ".." in Path(relative_script).parts:
        raise ValueError("document skill script path is unsafe")
    serialized_args = json.dumps(args or {}, sort_keys=True, separators=(",", ":"))
    return (
        "import json\n"
        "import runpy\n"
        "import sys\n"
        "import tempfile\n"
        "import zipfile\n"
        "from pathlib import Path\n"
        "bundle_path = Path('/inputs') / " + repr(bundle_name) + "\n"
        "with tempfile.TemporaryDirectory() as temporary_directory:\n"
        "    root = Path(temporary_directory)\n"
        "    with zipfile.ZipFile(bundle_path) as archive:\n"
        "        archive.extractall(root)\n"
        "    script_path = root / " + repr(relative_script) + "\n"
        "    if not script_path.is_file():\n"
        "        raise RuntimeError('resolved document skill script is absent')\n"
        "    import os\n"
        "    os.chdir(root)\n"
        "    skill_args = json.loads("
        + repr(serialized_args)
        + ")\n"
        + ("    skill_args['document'] = str(Path('/inputs') / " + repr(document_name) + ")\n" if document_name else "")
        + "    if isinstance(skill_args, dict):\n"
        + "        argv = []\n"
        + "        for key in sorted(skill_args):\n"
        + "            argv.extend([f'--{key.replace(chr(95), chr(45))}', str(skill_args[key])])\n"
        + "    else:\n"
        + "        argv = [str(value) for value in skill_args]\n"
        + "    sys.argv = [str(script_path), *argv]\n"
        + "    runpy.run_path(str(script_path), run_name='__main__', init_globals={'skill_args': skill_args})\n"
    )
