from __future__ import annotations

from typing import Any, Protocol, cast

from azure.cosmos.aio import ContainerProxy
from azure.cosmos.exceptions import CosmosResourceNotFoundError
from eda_api.auth.models import Principal
from pydantic import BaseModel, ConfigDict, Field

from .models import camel_case

MAX_TODO_ITEMS = 200


class TodoItemView(BaseModel):
    model_config = ConfigDict(alias_generator=camel_case, populate_by_name=True, extra="ignore", frozen=True)

    id: int = Field(ge=0)
    title: str = Field(min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=2000)
    is_complete: bool = False


class SessionTodoReader(Protocol):
    async def list_todos(self, principal: Principal, session_id: str) -> tuple[TodoItemView, ...]: ...


class InMemorySessionTodoReader:
    def __init__(self) -> None:
        self._todos: dict[tuple[str, str, str], tuple[TodoItemView, ...]] = {}

    def set(self, principal: Principal, session_id: str, items: tuple[TodoItemView, ...]) -> None:
        self._todos[self._key(principal, session_id)] = items

    async def list_todos(self, principal: Principal, session_id: str) -> tuple[TodoItemView, ...]:
        return self._todos.get(self._key(principal, session_id), ())

    @staticmethod
    def _key(principal: Principal, session_id: str) -> tuple[str, str, str]:
        return str(principal.tenant_id), str(principal.owner_object_id), session_id


class CosmosSessionTodoReader:
    """Reads the todo projection the worker history provider writes for a session."""

    def __init__(self, workspace: ContainerProxy) -> None:
        self._workspace = workspace

    async def list_todos(self, principal: Principal, session_id: str) -> tuple[TodoItemView, ...]:
        partition_key = [str(principal.tenant_id), str(principal.owner_object_id), session_id]
        try:
            raw = await self._workspace.read_item(item="todo-projection", partition_key=partition_key)
        except CosmosResourceNotFoundError:
            return ()
        record = cast(dict[str, Any], raw)
        items = record.get("items")
        if not isinstance(items, list):
            return ()
        views: list[TodoItemView] = []
        for entry in cast(list[Any], items)[:MAX_TODO_ITEMS]:
            if isinstance(entry, dict):
                views.append(TodoItemView.model_validate(cast(dict[str, Any], entry)))
        return tuple(views)
