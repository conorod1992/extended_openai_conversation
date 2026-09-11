"""Provider failure and recovery acceptance through real HA and SDK wire."""

from __future__ import annotations

from typing import Any

import pytest

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
    API_MODE_RESPONSES,
)
from homeassistant.components import conversation
from homeassistant.core import HomeAssistant
from tests_real_ha.test_provider_wire_e2e import (
    _agent,
    _chat_sse_text,
    _install_wire,
    _responses_sse_text,
    _say,
    _speech,
)


@pytest.mark.parametrize(
    ("api_mode", "success_reply", "expected_path"),
    [
        (
            API_MODE_CHAT_COMPLETIONS,
            _chat_sse_text("The provider recovered successfully."),
            "/v1/chat/completions",
        ),
        (
            API_MODE_RESPONSES,
            _responses_sse_text("The provider recovered successfully."),
            "/v1/responses",
        ),
    ],
)
async def test_provider_failure_recovers_on_next_turn_same_loaded_agent(
    hass: HomeAssistant,
    monkeypatch: Any,
    api_mode: str,
    success_reply: bytes,
    expected_path: str,
) -> None:
    """A failed provider turn must not poison the next turn on the loaded agent."""
    agent = await _agent(hass, api_mode)
    assert agent is not None

    wire = _install_wire(
        monkeypatch,
        agent,
        [
            (
                400,
                {
                    "error": {
                        "message": "provider recovery acceptance failure",
                        "type": "invalid_request_error",
                        "param": None,
                        "code": "provider_recovery_test",
                    }
                },
            ),
            success_reply,
        ],
    )

    failed = await _say(hass, agent)

    assert failed.response.error_code is not None
    failed_speech = failed.response.as_dict()["speech"]["plain"]["speech"]
    assert "problem talking to OpenAI" in failed_speech
    assert "provider recovery acceptance failure" in failed_speech
    assert "provider_recovery_test" in failed_speech
    assert len(wire.requests) == 1
    assert wire.requests[0]["path"] == expected_path
    assert conversation.async_get_agent(hass, agent.entry.entry_id) is agent

    recovered = await _say(hass, agent)

    assert _speech(recovered) == "The provider recovered successfully."
    assert conversation.async_get_agent(hass, agent.entry.entry_id) is agent
    assert [request["path"] for request in wire.requests] == [
        expected_path,
        expected_path,
    ]
