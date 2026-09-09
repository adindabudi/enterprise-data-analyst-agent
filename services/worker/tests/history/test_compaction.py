from eda_worker.history.compaction import TriggeredCompactor


def test_persisted_compaction_triggers_at_112k_and_targets_96k() -> None:
    messages = [{"message_id": f"msg_{index:08d}", "tokens": 1000, "text": "x"} for index in range(113)]
    compactor = TriggeredCompactor(trigger_tokens=112000, target_tokens=96000)

    changed = compactor(messages)

    assert changed is True
    assert sum(message["tokens"] for message in messages) <= 96000


def test_before_call_strategy_enforces_128k() -> None:
    messages = [{"message_id": f"msg_{index:08d}", "tokens": 1000, "text": "x"} for index in range(129)]
    compactor = TriggeredCompactor(trigger_tokens=128000, target_tokens=128000)

    compactor(messages)

    assert sum(message["tokens"] for message in messages) <= 128000
