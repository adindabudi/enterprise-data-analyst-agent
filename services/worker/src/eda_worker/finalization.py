from __future__ import annotations

import hashlib
import logging
from collections.abc import Sequence
from typing import Protocol

from eda_contracts import ArtifactKind, ArtifactRef
from eda_runtime_state.messages import CanonicalMessage
from eda_runtime_state.models import TaskRecord

from eda_worker.metrics import record_publish_structural

logger = logging.getLogger(__name__)
MAX_LISTED_TODOS = 5
OUTPUT_PROFILES = {
    ArtifactKind.HTML: ("web_artifact_html",),
    ArtifactKind.XLSX: ("core_xlsx", "document_xlsx"),
    ArtifactKind.XLSM: ("core_xlsx",),
    ArtifactKind.DOCX: ("document_docx",),
    ArtifactKind.PPTX: ("document_pptx",),
    ArtifactKind.PDF: ("document_pdf",),
    ArtifactKind.PNG: ("core_chart", "core_mermaid"),
    ArtifactKind.SVG: ("core_chart", "core_mermaid"),
    ArtifactKind.MERMAID: ("core_mermaid",),
}


class RuntimeFinalizerRepository(Protocol):
    async def resolve_task(self, task_id: str) -> TaskRecord | None: ...

    async def set_final_message(self, task_id: str, message_id: str) -> TaskRecord: ...


class PublishedArtifactReader(Protocol):
    async def published_refs(
        self,
        task_id: str,
        *,
        validation_profile: str | None = None,
    ) -> tuple[ArtifactRef, ...]: ...


class CanonicalMessageWriter(Protocol):
    async def append_canonical(self, message: CanonicalMessage) -> None: ...


class ActiveSandboxCanceller(Protocol):
    async def stop_task(self, task_id: str) -> None: ...


class OpenTodoReader(Protocol):
    async def open_todos(self, task: TaskRecord) -> tuple[str, ...]: ...


def unfinished_note(open_todos: Sequence[str]) -> str:
    """The agent wrote these steps itself, so leaving them open is its own admission."""
    if not open_todos:
        return ""
    listed = "; ".join(open_todos[:MAX_LISTED_TODOS])
    return f"\n\nThis run stopped before finishing its own plan. Still open: {listed}."


class CoreTaskFinalizer:
    def __init__(
        self,
        runtime: RuntimeFinalizerRepository,
        artifacts: PublishedArtifactReader,
        messages: CanonicalMessageWriter,
        sandbox: ActiveSandboxCanceller,
        todos: OpenTodoReader | None = None,
    ) -> None:
        self._runtime = runtime
        self._artifacts = artifacts
        self._messages = messages
        self._sandbox = sandbox
        self._todos = todos

    async def validate_outputs(self, payload: dict[str, object]) -> dict[str, str]:
        task_id = self._task_id(payload)
        required_profile = payload.get("requiredProfile")
        artifacts_required = True
        if required_profile is None:
            artifacts = await self._artifacts.published_refs(task_id)
        elif required_profile == "web_artifact_html":
            artifacts = tuple(
                artifact
                for artifact in await self._artifacts.published_refs(
                    task_id,
                    validation_profile="web_artifact_html",
                )
                if artifact.kind is ArtifactKind.HTML
            )
        else:
            raise ValueError("unsupported required output profile")
        if required_profile is not None or payload.get("requireOutputContract"):
            task = await self._runtime.resolve_task(task_id)
            if task is None:
                raise ValueError("task is unavailable for validation")
            if payload.get("requireOutputContract") and task.required_outputs is None:
                record_publish_structural(passed=False)
                return {"outcome": "failed", "reportRef": "missing-output-contract"}
            missing: list[str] = []
            for requirement in task.required_outputs or ():
                verified: set[str] = set()
                for profile in OUTPUT_PROFILES[requirement.kind]:
                    verified.update(
                        artifact.artifact_id
                        for artifact in await self._artifacts.published_refs(task_id, validation_profile=profile)
                        if artifact.kind is requirement.kind
                    )
                if len(verified) < requirement.minimum_count:
                    missing.append(f"{requirement.kind.value}({len(verified)}/{requirement.minimum_count})")
            if missing:
                record_publish_structural(passed=False)
                return {"outcome": "failed", "reportRef": f"missing-required-outputs:{','.join(missing)}"}
            if self._todos is not None and await self._todos.open_todos(task):
                record_publish_structural(passed=False)
                return {"outcome": "failed", "reportRef": "unfinished-plan"}
            artifacts_required = required_profile is not None or bool(task.required_outputs)
        passed = bool(artifacts) or not artifacts_required
        record_publish_structural(passed=passed)
        if not passed:
            return {"outcome": "failed", "reportRef": "no-published-artifacts"}
        return {"outcome": "passed", "reportRef": self._artifact_set_ref(artifacts)}

    async def publish_outputs(self, payload: dict[str, object]) -> dict[str, str]:
        task_id = self._task_id(payload)
        task = await self._runtime.resolve_task(task_id)
        if task is None:
            raise ValueError("task is unavailable for publication")
        artifacts = await self._artifacts.published_refs(task_id)
        if not artifacts:
            raise ValueError("task has no validated published artifacts")
        message_id = f"msg_{hashlib.sha256(f'{task_id}|final'.encode()).hexdigest()[:32]}"
        message = CanonicalMessage(
            id=message_id,
            tenant_id=str(task.tenant_id),
            owner_object_id=str(task.owner_object_id),
            session_id=task.session_id,
            task_id=task.id,
            role="assistant",
            text=f"Analysis completed. {len(artifacts)} validated artifact(s) are ready.",
            created_at=task.created_at,
        )
        await self._messages.append_canonical(message)
        await self._runtime.set_final_message(task_id, message.id)
        await self._sandbox.stop_task(task_id)
        return {"finalMessageId": message.id}

    async def complete_chat(self, payload: dict[str, object]) -> dict[str, str]:
        task_id = self._task_id(payload)
        text = payload.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > 64_000:
            raise ValueError("chat completion text is invalid")
        task = await self._runtime.resolve_task(task_id)
        if task is None:
            raise ValueError("task is unavailable for chat completion")
        message_id = f"msg_{hashlib.sha256(f'{task_id}|chat-final'.encode()).hexdigest()[:32]}"
        message = CanonicalMessage(
            id=message_id,
            tenant_id=str(task.tenant_id),
            owner_object_id=str(task.owner_object_id),
            session_id=task.session_id,
            task_id=task.id,
            role="assistant",
            text=f"{text.strip()}{unfinished_note(await self._open_todos(task))}",
            created_at=task.created_at,
        )
        await self._messages.append_canonical(message)
        await self._runtime.set_final_message(task_id, message.id)
        await self._sandbox.stop_task(task_id)
        return {"finalMessageId": message.id}

    async def cancel_task(self, payload: dict[str, object]) -> dict[str, str]:
        task_id = self._task_id(payload)
        await self._sandbox.stop_task(task_id)
        return {"outcome": "cancelled"}

    async def fail_task(self, payload: dict[str, object]) -> dict[str, str]:
        task_id = self._task_id(payload)
        await self._sandbox.stop_task(task_id)
        return {"outcome": "failed"}

    async def _open_todos(self, task: TaskRecord) -> tuple[str, ...]:
        if self._todos is None:
            return ()
        try:
            return await self._todos.open_todos(task)
        except Exception:
            # A finished run is worth more than a note about it.
            logger.exception("open todo lookup failed for task %s", task.id)
            return ()

    @staticmethod
    def _task_id(payload: dict[str, object]) -> str:
        task_id = payload.get("taskId")
        if not isinstance(task_id, str) or not task_id.startswith("task_"):
            raise ValueError("finalizer activity payload is invalid")
        return task_id

    @staticmethod
    def _artifact_set_ref(artifacts: Sequence[ArtifactRef]) -> str:
        material = "|".join(f"{ref.artifact_id}@{ref.version}@{ref.sha256}" for ref in artifacts)
        return f"artifact-set:{hashlib.sha256(material.encode()).hexdigest()}"
