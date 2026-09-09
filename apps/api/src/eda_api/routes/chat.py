from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated, Literal, Self

from eda_api.auth.dependencies import CsrfPrincipalDep
from eda_api.chat.service import InteractiveChatService
from eda_api.dependencies import interactive_chat_service, workspace_repository
from eda_api.storage.workspace import WorkspaceRepository
from eda_runtime_state.models import TaskPartition
from fastapi import APIRouter, Depends, Header, HTTPException, Path, status
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel, ConfigDict, Field, model_validator

router = APIRouter(prefix="/api/sessions", tags=["chat"])

SessionIdPath = Annotated[str, Path(pattern=r"^ses_[A-Za-z0-9_-]{16,}$")]
IdempotencyKeyHeader = Annotated[str, Header(alias="Idempotency-Key", pattern=r"^[!-~]{8,128}$")]
MAX_CONTEXT_MESSAGE_CHARS = 4_000
MAX_CONTEXT_CHARS = 12_000


class ChatHistoryMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role: Literal["user", "assistant"]
    text: str = Field(min_length=1, max_length=MAX_CONTEXT_MESSAGE_CHARS)


class InteractiveChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    message_id: str = Field(alias="messageId", pattern=r"^msg_[A-Za-z0-9_-]{8,}$")
    history: tuple[ChatHistoryMessage, ...] = Field(default=(), max_length=10)

    @model_validator(mode="after")
    def validate_context_size(self) -> Self:
        if sum(len(message.text) for message in self.history) > MAX_CONTEXT_CHARS:
            raise ValueError("interactive chat history exceeds the context limit")
        return self


@dataclass(frozen=True)
class ChatInvocation:
    service: InteractiveChatService
    partition: TaskPartition
    message_id: str
    history: tuple[dict[str, str], ...]
    idempotency_key: str


async def prepare_chat(
    session_id: SessionIdPath,
    request: InteractiveChatRequest,
    principal: CsrfPrincipalDep,
    repository: Annotated[WorkspaceRepository, Depends(workspace_repository)],
    service: Annotated[InteractiveChatService | None, Depends(interactive_chat_service)],
    idempotency_key: IdempotencyKeyHeader,
) -> ChatInvocation:
    if await repository.get_session(principal, session_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    if service is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
    return ChatInvocation(
        service=service,
        partition=TaskPartition(
            tenant_id=principal.tenant_id,
            owner_object_id=principal.owner_object_id,
            session_id=session_id,
        ),
        message_id=request.message_id,
        history=tuple(message.model_dump(mode="json") for message in request.history),
        idempotency_key=idempotency_key,
    )


@router.post("/{session_id}/chat", response_class=EventSourceResponse)
async def stream_chat(
    invocation: Annotated[ChatInvocation, Depends(prepare_chat)],
) -> AsyncIterator[ServerSentEvent]:
    async for update in invocation.service.stream(
        partition=invocation.partition,
        message_id=invocation.message_id,
        history=invocation.history,
        idempotency_key=invocation.idempotency_key,
    ):
        yield ServerSentEvent(event=update.event, data=update.data)
