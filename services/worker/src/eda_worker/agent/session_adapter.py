from __future__ import annotations

from collections.abc import Awaitable, Mapping, Sequence
from typing import Any, Protocol, cast
from uuid import UUID

from agent_framework import (
    AgentRunInputs,
    AgentSession,
    Content,
    Message,
    ResponseStream,
    ServiceSessionId,
    enqueue_messages,
)
from eda_contracts.controls import CommandKind
from eda_worker.fabric.contracts import FabricPrincipal
from eda_worker.history.models import CanonicalMessage, SessionPartition
from eda_worker.model.profiles import ModelContract, WorkClass

PHASE_WORK_CLASS = {
    "chat": WorkClass.ANALYSIS,
    "planning": WorkClass.CLARIFICATION,
    "acquiring_data": WorkClass.ANALYSIS,
    "analyzing": WorkClass.ANALYSIS,
    "generating": WorkClass.ARTIFACT,
    "validating": WorkClass.VALIDATION,
}
TRUSTED_OPTION_KEYS = {"task_id", "phase", "work_class", "pending_command_ids", "response_format"}


class RuntimeRepository(Protocol):
    async def resolve_task(self, task_id: str) -> ResolvedTask | None: ...


class ResolvedTask(Protocol):
    @property
    def tenant_id(self) -> UUID: ...

    @property
    def owner_object_id(self) -> UUID: ...

    @property
    def session_id(self) -> str: ...


class StoredProjection(Protocol):
    @property
    def agent_session(self) -> dict[str, Any]: ...


class ProjectionRepository(Protocol):
    async def load_projection(self, partition: SessionPartition) -> StoredProjection | None: ...

    async def load_canonical(
        self, partition: SessionPartition, message_ids: Sequence[str]
    ) -> list[CanonicalMessage | None]: ...


class BoundCommand(Protocol):
    @property
    def id(self) -> str: ...

    @property
    def task_id(self) -> str: ...

    @property
    def sequence(self) -> int: ...

    @property
    def kind(self) -> CommandKind: ...

    @property
    def text(self) -> str | None: ...


class BoundCommandRepository(Protocol):
    async def commands(self, task_id: str, command_ids: list[str]) -> Sequence[BoundCommand]: ...


class SessionHydratingAgent:
    def __init__(
        self,
        *,
        harness: Any,
        runtime_repository: RuntimeRepository,
        projection_repository: ProjectionRepository,
        command_repository: BoundCommandRepository,
        model_contract: ModelContract,
        product_audience: UUID,
    ) -> None:
        self._harness = harness
        self.id = cast(str, harness.id)
        self.name: str | None = cast(str | None, harness.name)
        self.description: str | None = cast(str | None, harness.description)
        self._runtime_repository = runtime_repository
        self._projection_repository = projection_repository
        self._command_repository = command_repository
        self._model_contract = model_contract
        self._product_audience = product_audience

    def create_session(self, *, session_id: str | None = None) -> AgentSession:
        return cast(AgentSession, self._harness.create_session(session_id=session_id))

    def get_session(
        self,
        service_session_id: str | ServiceSessionId,
        *,
        session_id: str | None = None,
    ) -> AgentSession:
        return cast(AgentSession, self._harness.get_session(service_session_id, session_id=session_id))

    def run(
        self,
        messages: AgentRunInputs | None = None,
        *,
        stream: bool = False,
        session: AgentSession | None = None,
        function_invocation_kwargs: Mapping[str, Any] | None = None,
        client_kwargs: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        if session is not None:
            raise ValueError("session hydrator does not accept an outer session")
        if function_invocation_kwargs is not None or client_kwargs is not None:
            raise ValueError("session hydrator does not accept caller-supplied invocation kwargs")
        unknown_kwargs = set(kwargs) - {"options"}
        if unknown_kwargs:
            raise ValueError(f"session hydrator received unsupported arguments: {sorted(unknown_kwargs)}")
        raw_options: object = kwargs.get("options")
        if not isinstance(raw_options, dict):
            raise ValueError("session hydrator requires durable options")
        options = cast(dict[str, object], raw_options)
        current_request = self._current_request(messages)
        if stream:
            stream_awaitable: Awaitable[ResponseStream[Any, Any]] = self._open_stream(current_request, options)
            return ResponseStream[Any, Any].from_awaitable(stream_awaitable)
        return self._run_once(current_request, options)

    async def _run_once(self, message: AgentRunInputs, options: dict[str, object]) -> Any:
        restored, phase, model_options, principal = await self._prepare(options)
        task_id = cast(str, model_options.pop("task_id"))
        result = self._harness.run(
            message,
            session=restored,
            stream=False,
            options=model_options,
            function_invocation_kwargs={"task_id": task_id, "phase": phase, "principal": principal},
        )
        return await result

    async def _open_stream(self, message: AgentRunInputs, options: dict[str, object]) -> ResponseStream[Any, Any]:
        restored, phase, model_options, principal = await self._prepare(options)
        task_id = cast(str, model_options.pop("task_id"))
        result = self._harness.run(
            message,
            session=restored,
            stream=True,
            options=model_options,
            function_invocation_kwargs={"task_id": task_id, "phase": phase, "principal": principal},
        )
        if not isinstance(result, ResponseStream):
            raise TypeError("streaming harness run must return ResponseStream")
        return cast(ResponseStream[Any, Any], result)

    @staticmethod
    def _current_request(message: AgentRunInputs | None) -> AgentRunInputs:
        if message is None:
            raise ValueError("session hydrator requires a current request")
        if isinstance(message, (str, Content, Message)):
            return message
        replayed_messages: list[str | Content | Message] = list(message)
        if not replayed_messages:
            raise ValueError("session hydrator requires a current request")
        return [replayed_messages[-1]]

    async def _prepare(self, options: dict[str, object]) -> tuple[AgentSession, str, dict[str, Any], FabricPrincipal]:
        unknown_keys = set(options) - TRUSTED_OPTION_KEYS
        if unknown_keys:
            raise ValueError(f"caller-supplied model options are forbidden: {sorted(unknown_keys)}")
        task_id = options.get("task_id")
        phase = options.get("phase")
        raw_work_class = options.get("work_class")
        raw_command_ids = options.get("pending_command_ids")
        if not isinstance(task_id, str) or not isinstance(phase, str) or not isinstance(raw_work_class, str):
            raise ValueError("session hydrator requires trusted task, phase, and work class options")
        if not isinstance(raw_command_ids, list):
            raise ValueError("session hydrator requires trusted pending command IDs")
        raw_command_values = cast(list[object], raw_command_ids)
        if not all(isinstance(value, str) for value in raw_command_values):
            raise ValueError("session hydrator requires trusted pending command IDs")
        try:
            work_class = WorkClass(raw_work_class)
        except ValueError as error:
            raise ValueError("session hydrator work class is invalid") from error
        if PHASE_WORK_CLASS.get(phase) is not work_class:
            raise ValueError("phase and work class do not match")

        task = await self._runtime_repository.resolve_task(task_id)
        if task is None:
            raise ValueError("task is unavailable for session hydration")
        principal = FabricPrincipal(
            tenant_id=task.tenant_id,
            owner_object_id=task.owner_object_id,
            audience=self._product_audience,
        )
        partition = SessionPartition(
            tenant_id=task.tenant_id,
            owner_object_id=task.owner_object_id,
            session_id=task.session_id,
        )
        projection = await self._projection_repository.load_projection(partition)
        restored = self._restore_session(partition.session_id, projection)
        if projection is None:
            await self._enqueue_source_message(restored, partition, task)
        projection_scope = restored.state.setdefault("projection", {})
        if not isinstance(projection_scope, dict):
            raise ValueError("persisted projection provider state is malformed")
        projection_scope["partition"] = partition.model_dump(mode="json")
        task_state_scope = restored.state.setdefault("task-state", {})
        if not isinstance(task_state_scope, dict):
            raise ValueError("persisted task-state provider state is malformed")
        task_state_scope["task_id"] = task_id
        task_state_scope["phase"] = phase
        command_ids = [cast(str, value) for value in raw_command_values]
        commands = await self._command_repository.commands(task_id, command_ids)
        self._enqueue_commands(restored, task_id, command_ids, commands)

        model_options = self._model_contract.options_for(work_class)
        model_options["task_id"] = task_id
        response_format = options.get("response_format")
        if response_format is not None:
            model_options["response_format"] = response_format
        return restored, phase, model_options, principal

    async def _enqueue_source_message(
        self,
        session: AgentSession,
        partition: SessionPartition,
        task: ResolvedTask,
    ) -> None:
        source_message_id = getattr(task, "source_message_id", None)
        if source_message_id is None:
            return
        if not isinstance(source_message_id, str):
            raise ValueError("task source message ID is malformed")
        loaded = await self._projection_repository.load_canonical(partition, [source_message_id])
        if len(loaded) != 1 or loaded[0] is None:
            raise ValueError("task source message is unavailable")
        source = loaded[0]
        if source.role != "user" or source.session_id != partition.session_id:
            raise ValueError("task source message is invalid")
        messages: list[Message] = []
        handoff_context = getattr(task, "handoff_context", None)
        if isinstance(handoff_context, str) and handoff_context:
            # The projection rejects two messages that share an ID but not content, and a durable
            # run replays, so this needs an ID of its own derived from the turn it belongs to.
            messages.append(Message(role="user", contents=[handoff_context], message_id=f"{source.id}.handoff"))
        messages.append(Message(role="user", contents=[source.text], message_id=source.id))
        enqueue_messages(session, messages)

    @staticmethod
    def _restore_session(session_id: str, projection: StoredProjection | None) -> AgentSession:
        if projection is None:
            return AgentSession(session_id=session_id)
        session_document = dict(projection.agent_session)
        session_document.setdefault("type", "session")
        session_document.setdefault("session_id", session_id)
        session_document.setdefault("service_session_id", None)
        session_document.setdefault("state", {})
        restored = AgentSession.from_dict(session_document)
        if restored.session_id != session_id:
            raise ValueError("persisted agent session belongs to another analysis")
        return restored

    @staticmethod
    def _enqueue_commands(
        session: AgentSession,
        task_id: str,
        command_ids: list[str],
        commands: Sequence[BoundCommand],
    ) -> None:
        if len(commands) != len(command_ids):
            raise ValueError("pending command set is incomplete")
        ordered = sorted(commands, key=lambda command: command.sequence)
        if [command.id for command in ordered] != command_ids:
            raise ValueError("pending commands are duplicated or out of order")
        messages: list[Message] = []
        for command in ordered:
            if command.task_id != task_id:
                raise ValueError("pending command belongs to another task")
            kind_value = command.kind.value
            text = command.text
            if kind_value == CommandKind.STEER.value:
                if not isinstance(text, str) or not text:
                    raise ValueError("steering command text is missing")
                messages.append(Message(role="user", contents=[text], message_id=command.id))
        if messages:
            enqueue_messages(session, messages)
