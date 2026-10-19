"""Sibling-operation checks from the bounded nine-finding review."""

import asyncio
from collections import deque
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import (
    intercom,
    management_function_quarantine as quarantine,
)
from custom_components.extended_openai_conversation_responses.agent_config import (
    AgentConfigError,
)
from custom_components.extended_openai_conversation_responses.context_usage_hardening import (
    ContextInputMeasurement,
    prepared_input_revision,
)
from custom_components.extended_openai_conversation_responses.speech import (
    process_speech_text,
)
from tests.test_intercom import _coverage_message
from tests.test_management_function_quarantine import _mixed_unknown_native_data
from tests.test_speech import _config, _stream


@pytest.mark.parametrize("field", ["content", "image_url"])
def test_context_measurements_reject_in_place_payload_changes(field):
    payload = [
        {"role": "user", "content": [{"type": "input_image", "image_url": "first"}]}
    ]
    measurement = ContextInputMeasurement(5000, 1, prepared_input_revision(payload))
    assert measurement.applies_to(deepcopy(payload))
    if field == "content":
        payload[0][field] = "Changed text"
    else:
        payload[0]["content"][0][field] = "second"
    assert not measurement.applies_to(payload)


def test_groups_alone_cannot_block_unrelated_settings(hass):
    raw, valid, _ = _mixed_unknown_native_data()
    raw["functions"] = [valid]
    raw["function_groups"] = [{"id": "broken", "functions": ["missing"]}]
    original = deepcopy(raw)
    saved, display = quarantine.merge_unchanged_function_configuration(
        SimpleNamespace(data=raw, title="Agent"), {"knowledge_enabled": True}
    )
    assert saved == {**original, "knowledge_enabled": True}
    assert display["function_groups"] == []
    with pytest.raises(AgentConfigError):
        quarantine.merge_unchanged_function_configuration(
            SimpleNamespace(data=raw, title="Agent"), {"context_threshold": -1}
        )


@pytest.mark.parametrize(
    "text,expected",
    [
        ("*outer **inner***", "outer inner"),
        ("**outer *inner***", "outer inner"),
        ("2 ** 3 and 2 * 3", "2 ** 3 and 2 * 3"),
        (r"\*literal\*", r"\*literal\*"),
        ("```\nplain code\n```", "plain code"),
    ],
)
def test_nested_speech_sibling_constructs(text, expected):
    assert process_speech_text(text, _config()) == expected
    for split in range(len(text) + 1):
        assert _stream([text[:split], text[split:]]).strip() == expected


async def test_stalled_broadcast_fails_and_does_not_advance_busy_satellite(
    hass, monkeypatch
):
    manager = intercom.IntercomManager(hass)
    manager._enabled = True
    entity_id = "assist_satellite.kitchen"
    first = _coverage_message(entity_id)
    second = _coverage_message(entity_id)
    second.id = "next"
    manager._queues[entity_id] = deque([first, second])
    state = SimpleNamespace(state="idle")
    hass.states.get.return_value = state
    monkeypatch.setattr(intercom, "IDLE_STABILITY_SECONDS", 0)
    monkeypatch.setattr(intercom, "_announcement_timeout_seconds", lambda _: 0.01)

    async def stalled(*args, **kwargs):
        state.state = "responding"
        await asyncio.Event().wait()

    hass.services.async_call.side_effect = stalled
    await manager._async_drain(entity_id)
    assert first.deliveries[entity_id].status == "failed"
    assert second.deliveries[entity_id].status == "queued_busy"
    hass.services.async_call.assert_awaited_once()


async def test_knowledge_toggle_preserves_quarantined_source_bytes(hass, monkeypatch):
    from custom_components.extended_openai_conversation_responses import management_ui
    from tests.test_management_ui import _entry_pair

    entry, subentry = _entry_pair()
    raw, _, _ = _mixed_unknown_native_data()
    subentry.data = raw
    original = deepcopy(raw)
    monkeypatch.setattr(management_ui, "entry_and_agent", lambda *_: (entry, subentry))
    monkeypatch.setattr(
        management_ui,
        "async_get_knowledge",
        AsyncMock(return_value=SimpleNamespace(total_source_count=0)),
    )
    result = await management_ui.async_management_command(
        hass,
        "admin",
        True,
        {
            "section": "knowledge",
            "action": "set_enabled",
            "enabled": True,
            "entry_id": entry.entry_id,
            "subentry_id": subentry.subentry_id,
        },
    )
    assert result["knowledge_enabled"] is True
    saved = hass.config_entries.async_update_subentry.call_args.kwargs["data"]
    assert saved == {**original, "knowledge_enabled": True}
