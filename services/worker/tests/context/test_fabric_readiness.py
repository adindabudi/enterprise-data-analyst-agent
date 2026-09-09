from __future__ import annotations

from types import SimpleNamespace

from eda_worker.context.fabric_readiness import FabricPendingValidationContextProvider


async def test_before_run_injects_pending_validation_note() -> None:
    calls: list[tuple[str, str]] = []
    context = SimpleNamespace(extend_instructions=lambda source_id, text: calls.append((source_id, text)))

    provider = FabricPendingValidationContextProvider()
    await provider.before_run(agent=None, session=None, context=context, state={})

    assert len(calls) == 1
    source_id, text = calls[0]
    assert source_id == "fabric-readiness"
    lowered = text.lower()
    assert "connected" in lowered
    assert "validat" in lowered
    assert "no source is configured" in lowered
