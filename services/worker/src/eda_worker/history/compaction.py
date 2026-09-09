from __future__ import annotations

from typing import Any

from agent_framework import (
    TokenBudgetComposedStrategy,
    TokenizerProtocol,
    ToolResultCompactionStrategy,
    TruncationStrategy,
)


class ContextInvariantError(RuntimeError):
    pass


class TriggeredCompactor:
    def __init__(self, *, trigger_tokens: int, target_tokens: int) -> None:
        if target_tokens > trigger_tokens:
            raise ValueError("target_tokens must not exceed trigger_tokens")
        self.trigger_tokens = trigger_tokens
        self.target_tokens = target_tokens

    def __call__(self, messages: list[dict[str, Any]]) -> bool:
        total = _token_count(messages)
        if total <= self.trigger_tokens:
            return False
        retained: list[dict[str, Any]] = []
        retained_tokens = 0
        for message in reversed(messages):
            tokens = _message_tokens(message)
            if retained_tokens + tokens > self.target_tokens:
                continue
            retained.append(message)
            retained_tokens += tokens
        messages[:] = list(reversed(retained))
        if _token_count(messages) > self.target_tokens:
            raise ContextInvariantError("compaction failed to restore the configured token target")
        return True


def _message_tokens(message: dict[str, Any]) -> int:
    value = message.get("tokens", 0)
    return value if isinstance(value, int) and value >= 0 else 0


def _token_count(messages: list[dict[str, Any]]) -> int:
    return sum(_message_tokens(message) for message in messages)


def hard_ceiling_compactor(tokenizer: TokenizerProtocol) -> TokenBudgetComposedStrategy:
    truncation = TruncationStrategy(max_n=128_000, compact_to=128_000, tokenizer=tokenizer)
    return TokenBudgetComposedStrategy(
        token_budget=128_000,
        tokenizer=tokenizer,
        strategies=[ToolResultCompactionStrategy(keep_last_tool_call_groups=0), truncation],
    )
