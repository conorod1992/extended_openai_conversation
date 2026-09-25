"""Nightly recorder/history degradation at the public Assist/provider boundary."""

from __future__ import annotations

from contextlib import contextmanager
import json
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockUser

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
    CONF_API_MODE,
    CONF_ARCHIVE_ENABLED,
    CONF_FUNCTION_TOOLS,
)
from custom_components.extended_openai_conversation_responses.functions import native
from homeassistant.components import conversation
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.core import HomeAssistant
from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry
from tests_real_ha.test_knowledge_provider_wire_e2e import _chat_sse_tool_call
from tests_real_ha.test_provider_wire_e2e import _chat_sse_text
from tests_stress.conftest import record
from tests_stress.provider_fault_transport import ProviderFaultTransport, WireStep
from tests_stress.test_provider_fault_matrix import _OWNER, _say


@pytest.mark.parametrize("degradation", ["unavailable", "erroring", "delayed"])
async def test_history_dependency_failure_is_tool_scoped_and_recovers(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    stress_trace: list[dict],
    degradation: str,
) -> None:
    """A recorder failure cannot fabricate history or poison the next Assist turn."""
    MockUser(id=_OWNER, name="History owner", is_owner=True).add_to_hass(hass)
    entity_id = "sensor.history_fault_probe"
    hass.states.async_set(entity_id, "ready")
    async_expose_entity(hass, conversation.DOMAIN, entity_id, True)
    tool_name = "nightly_get_history"
    entry = _make_entry(
        f"Nightly recorder degradation {degradation}",
        include_ai_task=False,
        conversation_options={
            CONF_API_MODE: API_MODE_CHAT_COMPLETIONS,
            CONF_ARCHIVE_ENABLED: True,
            CONF_FUNCTION_TOOLS: [
                {
                    "spec": {
                        "name": tool_name,
                        "description": "Read exposed entity history",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "entity_ids": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                }
                            },
                            "required": ["entity_ids"],
                        },
                    },
                    "function": {"type": "native", "name": "get_history"},
                    "enabled": True,
                }
            ],
        },
    )
    await _setup_entry(hass, entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert agent is not None
    assert agent._archive is not None

    calls = 0
    original = native.recorder.util.session_scope

    @contextmanager
    def failing_session(*args: Any, **kwargs: Any):
        nonlocal calls
        calls += 1
        if degradation == "unavailable":
            raise RuntimeError("recorder unloaded for nightly probe")
        if degradation == "erroring":
            raise OSError("history database unavailable for nightly probe")
        raise TimeoutError("history query delayed past deadline for nightly probe")
        yield  # pragma: no cover - contextmanager shape

    monkeypatch.setattr(native.recorder.util, "session_scope", failing_session)
    call_id = "call-history-fault"
    provider = ProviderFaultTransport(
        [
            WireStep(
                "sse",
                body=_chat_sse_tool_call(
                    call_id, tool_name, {"entity_ids": [entity_id]}
                ),
            ),
            WireStep("sse", body=_chat_sse_text("History is temporarily unavailable.")),
            WireStep("sse", body=_chat_sse_text("A later request still works.")),
        ]
    )
    provider.install(monkeypatch, agent)
    first = await _say(hass, agent, "Read recent history")
    assert first.response.error_code is None
    assert calls == 1
    assert first.response.as_dict()["speech"]["plain"]["speech"] == (
        "History is temporarily unavailable."
    )
    result = next(
        message
        for message in provider.requests[1]["body"]["messages"]
        if message.get("role") == "tool" and message.get("tool_call_id") == call_id
    )
    tool_result = json.loads(result["content"])
    assert "error" in str(tool_result).lower()
    assert "history_fault_probe" not in str(tool_result)
    assert agent._archive.stats()["turn_count"] == 1

    monkeypatch.setattr(native.recorder.util, "session_scope", original)
    second = await _say(hass, agent, "Ask a question without history")
    assert second.response.error_code is None
    assert second.response.as_dict()["speech"]["plain"]["speech"] == (
        "A later request still works."
    )
    assert agent._archive.stats()["turn_count"] == 2
    assert agent._usage.totals.successful_request_count == 3
    assert len(provider.requests) == 3
    record(stress_trace, "history_dependency", degradation=degradation, calls=calls)
