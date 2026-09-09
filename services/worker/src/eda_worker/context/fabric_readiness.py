from __future__ import annotations

from typing import Any

_PENDING_NOTE = (
    "An enterprise Fabric data source is connected but has not finished validation, so it is not "
    "queryable yet and no Fabric query tool is available. If the user asks about that data, explain "
    "that the source is connected but still validating; do not state that no source is configured."
)


class FabricPendingValidationContextProvider:
    """Runtime note for the CONFIGURED-but-not-READY state; keeps the fail-closed query gate intact."""

    source_id = "fabric-readiness"

    async def before_run(self, *, agent: object, session: object, context: Any, state: dict[str, object]) -> None:
        del agent, session, state
        context.extend_instructions(self.source_id, _PENDING_NOTE)

    async def after_run(self, *, agent: object, session: object, context: object, state: dict[str, object]) -> None:
        del agent, session, context, state
