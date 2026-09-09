from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from agent_framework import BaseChatClient, ChatMiddlewareLayer, ChatResponse, Content, FunctionInvocationLayer, Message


class RecordingChatClient(FunctionInvocationLayer, ChatMiddlewareLayer, BaseChatClient):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[dict[str, object]] = []

    def _inner_get_response(
        self,
        *,
        messages: Sequence[Message],
        stream: bool,
        options: Mapping[str, Any],
        **kwargs: Any,
    ) -> Any:
        if stream:
            raise ValueError("recording client supports nonstreaming integration calls only")

        async def response() -> ChatResponse[Any]:
            normalized_options = await self._validate_options(options)
            self.calls.append(
                {
                    "messages": list(messages),
                    "options": normalized_options,
                    "function_invocation_kwargs": dict(kwargs.get("function_invocation_kwargs") or {}),
                }
            )
            if len(self.calls) == 1:
                return ChatResponse(
                    messages=Message(
                        role="assistant",
                        contents=[
                            Content.from_text_reasoning(text="synthetic hidden reasoning"),
                            Content.from_function_call(
                                "call_inspect_1",
                                "inspect_artifact",
                                arguments={"artifact": "input-7"},
                            ),
                        ],
                    ),
                    model="recording-model",
                    conversation_id=None,
                    finish_reason="tool_calls",
                )
            return ChatResponse(
                messages=Message(role="assistant", contents=[Content.from_text("phase complete")]),
                model="recording-model",
                conversation_id=None,
                finish_reason="stop",
            )

        return response()


class RecoveryRecordingChatClient(FunctionInvocationLayer, ChatMiddlewareLayer, BaseChatClient):
    def __init__(self, response_text: str) -> None:
        super().__init__()
        self.response_text = response_text
        self.calls: list[dict[str, object]] = []

    def _inner_get_response(
        self,
        *,
        messages: Sequence[Message],
        stream: bool,
        options: Mapping[str, Any],
        **kwargs: Any,
    ) -> Any:
        if stream:
            raise ValueError("recovery client supports nonstreaming integration calls only")

        async def response() -> ChatResponse[Any]:
            normalized_options = await self._validate_options(options)
            self.calls.append(
                {
                    "messages": list(messages),
                    "options": normalized_options,
                    "function_invocation_kwargs": dict(kwargs.get("function_invocation_kwargs") or {}),
                }
            )
            return ChatResponse(
                messages=Message(role="assistant", contents=[Content.from_text(self.response_text)]),
                model="recording-model",
                conversation_id=None,
                finish_reason="stop",
            )

        return response()
