"""Conversation, Function Tool history, and AI Task automation semantics."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
    CONF_API_MODE,
    CONF_CHAT_MODEL,
    CONF_FUNCTION_GROUPS,
    CONF_FUNCTION_TOOLS,
    CONF_REASONING_EFFORT,
)
from homeassistant.components import automation, conversation, media_source
from homeassistant.core import Context, HomeAssistant
from homeassistant.setup import async_setup_component
from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry
from tests_real_ha.test_ai_task_runtime import FakeClient
from tests_real_ha.test_knowledge_provider_wire_e2e import _chat_sse_tool_call
from tests_real_ha.test_live_config_mutation_semantics import (
    _mutate_tool,
    _tool,
    _tool_revision,
)
from tests_real_ha.test_provider_wire_e2e import _chat_sse_text, _install_wire, _speech
from tests_stress.conftest import record


async def _say(
    hass: HomeAssistant,
    agent,
    text: str,
    conversation_id: str | None = None,
):
    return await conversation.async_converse(
        hass=hass,
        text=text,
        conversation_id=conversation_id,
        context=Context(),
        language="en",
        agent_id=agent.entry.entry_id,
    )


@pytest.mark.asyncio
async def test_real_automation_consumes_structured_ai_task_media_and_falsey_values(
    hass: HomeAssistant,
    tmp_path: Path,
    stress_trace: list[dict],
) -> None:
    """Automation -> AI Task -> media -> structured response -> later action is real."""
    from homeassistant.components import ai_task
    from homeassistant.helpers import entity_registry as er

    entry = _make_entry(
        "Automation AI Task runtime",
        include_ai_task=True,
        conversation_options={
            CONF_API_MODE: API_MODE_CHAT_COMPLETIONS,
            CONF_CHAT_MODEL: "gpt-5.6",
        },
    )
    await _setup_entry(hass, entry)
    task_subentry = next(
        subentry
        for subentry in entry.subentries.values()
        if subentry.subentry_type == "ai_task_data"
    )
    hass.config_entries.async_update_subentry(
        entry,
        task_subentry,
        data={
            **task_subentry.data,
            CONF_API_MODE: API_MODE_CHAT_COMPLETIONS,
            CONF_CHAT_MODEL: "gpt-5.6",
            CONF_REASONING_EFFORT: "none",
        },
    )
    await hass.async_block_till_done()
    entity_id = er.async_get(hass).async_get_entity_id(
        ai_task.DOMAIN,
        "extended_openai_conversation_responses",
        task_subentry.subentry_id,
    )
    assert entity_id is not None

    client = FakeClient(['{"count":0,"detected":false,"summary":"nothing detected"}'])
    entry.runtime_data = client

    image_path = tmp_path / "automation-camera.jpg"
    image_path.write_bytes(b"deterministic-automation-camera-bytes")
    result_events: list[dict] = []
    hass.bus.async_listen(
        "eoai_ai_task_automation_result",
        lambda event: result_events.append(dict(event.data)),
    )

    configuration = {
        "automation": [
            {
                "id": "eoai-ai-task-runtime",
                "alias": "EOAI AI Task runtime",
                "trigger": [
                    {
                        "platform": "event",
                        "event_type": "eoai_ai_task_automation_trigger",
                    }
                ],
                "action": [
                    {
                        "action": "ai_task.generate_data",
                        "data": {
                            "task_name": "Automation camera analysis",
                            "instructions": "Inspect the attached image and return the structured fields.",
                            "entity_id": entity_id,
                            "structure": {
                                "count": {
                                    "description": "Number of matching objects.",
                                    "selector": {"number": {}},
                                    "required": True,
                                },
                                "detected": {
                                    "description": "Whether anything was detected.",
                                    "selector": {"boolean": {}},
                                    "required": True,
                                },
                                "summary": {
                                    "description": "Short summary.",
                                    "selector": {"text": {}},
                                    "required": True,
                                },
                            },
                            "attachments": [
                                {
                                    "media_content_id": "media-source://local/automation-camera.jpg",
                                    "media_content_type": "image/jpeg",
                                }
                            ],
                        },
                        "response_variable": "generated",
                    },
                    {
                        "event": "eoai_ai_task_automation_result",
                        "event_data": {
                            "count": "{{ generated.data.count }}",
                            "detected": "{{ generated.data.detected }}",
                            "summary": "{{ generated.data.summary }}",
                            "conversation_id": "{{ generated.conversation_id }}",
                        },
                    },
                ],
            }
        ]
    }

    with patch(
        "homeassistant.components.media_source.async_resolve_media",
        return_value=media_source.PlayMedia(
            url="http://example.invalid/automation-camera.jpg",
            mime_type="image/jpeg",
            path=image_path,
        ),
    ):
        assert await async_setup_component(hass, automation.DOMAIN, configuration)
        await hass.async_block_till_done()
        hass.bus.async_fire("eoai_ai_task_automation_trigger")
        for _ in range(200):
            await hass.async_block_till_done()
            if result_events:
                break

    assert len(result_events) == 1
    event = result_events[0]
    assert event["count"] in (0, "0")
    assert event["detected"] in (False, "False", "false")
    assert event["summary"] == "nothing detected"
    assert event["conversation_id"]

    assert len(client.completions.calls) == 1
    request = client.completions.calls[0]
    assert request["response_format"]["type"] == "json_schema"
    user_message = next(
        message
        for message in reversed(request["messages"])
        if message.get("role") == "user"
    )
    assert isinstance(user_message["content"], list)
    assert any(part.get("type") == "image_url" for part in user_message["content"])
    assert any(
        part.get("type") == "image_url"
        and part["image_url"]["url"].startswith("data:image/jpeg;base64,")
        for part in user_message["content"]
    )
    record(
        stress_trace,
        "summary",
        layer="Real HA automation + AI Task + media",
        automation_ai_task_journeys=1,
        structured_falsey_fields=2,
        media_attachments=1,
        downstream_response_consumers=1,
    )


@pytest.mark.parametrize("mutation", ["rename", "delete"])
@pytest.mark.asyncio
async def test_completed_tool_history_survives_later_tool_rename_or_removal(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    stress_trace: list[dict],
    mutation: str,
) -> None:
    """Historical tool transcript remains serializable after config no longer has it."""
    old_name = "historical_status"
    new_name = "historical_status_v2"
    entry = _make_entry(
        f"Historical Function Tool {mutation}",
        include_ai_task=False,
        conversation_options={
            CONF_API_MODE: API_MODE_CHAT_COMPLETIONS,
            CONF_CHAT_MODEL: "gpt-5.6",
            CONF_FUNCTION_TOOLS: [_tool(old_name, "OLD_TOOL_RESULT")],
            CONF_FUNCTION_GROUPS: [],
        },
    )
    await _setup_entry(hass, entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert agent is not None

    first_wire = _install_wire(
        monkeypatch,
        agent,
        [
            _chat_sse_tool_call("historical-call", old_name, {}),
            _chat_sse_text("Historical tool completed."),
        ],
    )
    first = await _say(hass, agent, "Use the historical status tool.")
    assert _speech(first) == "Historical tool completed."
    assert first.conversation_id
    assert len(first_wire.requests) == 2

    second_body = first_wire.requests[1]["body"]
    historical_call = next(
        message
        for message in second_body["messages"]
        if message.get("role") == "assistant" and message.get("tool_calls")
    )
    assert historical_call["tool_calls"][0]["function"]["name"] == old_name
    historical_result = next(
        message
        for message in second_body["messages"]
        if message.get("role") == "tool"
    )
    assert historical_result["tool_call_id"] == "historical-call"
    assert "OLD_TOOL_RESULT" in historical_result["content"]

    revision = await _tool_revision(hass, agent)
    if mutation == "rename":
        await _mutate_tool(
            hass,
            agent,
            action="save",
            revision=revision,
            tool=_tool(new_name, "NEW_TOOL_RESULT"),
            original_name=old_name,
        )
    else:
        await _mutate_tool(
            hass,
            agent,
            action="delete",
            revision=revision,
            name=old_name,
            confirm=True,
        )
    await hass.async_block_till_done()
    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert agent is not None

    continuation_wire = _install_wire(
        monkeypatch,
        agent,
        [_chat_sse_text("Conversation continued safely.")],
    )
    continued = await _say(
        hass,
        agent,
        "Continue after the Function Tool configuration changed.",
        conversation_id=first.conversation_id,
    )
    assert _speech(continued) == "Conversation continued safely."
    assert continued.conversation_id == first.conversation_id
    assert len(continuation_wire.requests) == 1

    body = continuation_wire.requests[0]["body"]
    serialized = str(body["messages"])
    assert old_name in serialized
    assert "historical-call" in serialized
    assert "OLD_TOOL_RESULT" in serialized

    advertised = {
        item["function"]["name"]
        for item in body.get("tools", [])
        if item.get("type") == "function"
    }
    if mutation == "rename":
        assert new_name in advertised
        assert old_name not in advertised
    else:
        assert old_name not in advertised

    record(
        stress_trace,
        "summary",
        layer="Real HA Assist + provider wire",
        completed_historical_tool_transcripts=1,
        tool_configuration_mutations=1,
        continuation_after_tool_mutation=1,
        mutation=mutation,
    )
