"""Live continuity behavior through Home Assistant's conversation entry point."""

import pytest

from pytest_homeassistant_custom_component.common import MockUser

from custom_components.extended_openai_conversation_responses.const import (
    CONF_ARCHIVE_ENABLED,
    CONF_CONVERSATION_CONTINUITY,
    CONF_CONVERSATION_TIMEOUT_MINUTES,
    CONF_VOICE_DEVICE_MAPPINGS,
    CONF_VOICE_SCOPE_POLICY,
    CONVERSATION_CONTINUITY_DEVICE,
    CONVERSATION_CONTINUITY_USER,
    VOICE_POLICY_DEVICE_MAPPING,
)
from homeassistant.components import conversation
from homeassistant.components.conversation.chat_log import DATA_CHAT_LOGS
from homeassistant.core import Context
from homeassistant.helpers.chat_session import DATA_CHAT_SESSION
from tests_real_ha.test_cross_feature_acceptance import (
    _action,
    _agent,
    _provider,
    _rule,
    _say,
    _speech,
)
from tests_real_ha.test_provisional_conversation_ownership import _expire


async def test_mapped_and_authenticated_assist_keep_their_native_sessions(
    hass, monkeypatch
):
    owner = MockUser(id="same-retained-owner")
    owner.add_to_hass(hass)
    device_id = "mapped-assist-device"
    agent = await _agent(
        hass,
        **{
            CONF_CONVERSATION_CONTINUITY: CONVERSATION_CONTINUITY_USER,
            CONF_VOICE_SCOPE_POLICY: VOICE_POLICY_DEVICE_MAPPING,
            CONF_VOICE_DEVICE_MAPPINGS: {device_id: owner.id},
        },
    )
    sent = _provider(monkeypatch, agent, ["Ready."] * 4)

    async def say(text, user_id):
        answer = await conversation.async_converse(
            hass=hass,
            text=text,
            conversation_id=None,
            context=Context(user_id=user_id),
            language="en",
            agent_id=agent.entry.entry_id,
            device_id=device_id,
        )
        assert _speech(answer) == "Ready."
        return answer.conversation_id

    voice_id = await say("Voice opening witness", None)
    auth_id = await say("Authenticated opening witness", owner.id)
    assert voice_id != auth_id
    assert voice_id == await say("Voice followup", None)
    assert auth_id == await say("Authenticated followup", owner.id)
    native = hass.data[DATA_CHAT_SESSION]
    if hasattr(native[voice_id], "user_id"):
        assert native[voice_id].user_id is None
        assert native[auth_id].user_id == owner.id
    assert "Voice opening witness" in str(sent[2]["messages"])
    assert "Authenticated opening witness" not in str(sent[2]["messages"])
    assert "Authenticated opening witness" in str(sent[3]["messages"])
    assert "Voice opening witness" not in str(sent[3]["messages"])


async def test_restored_native_log_traces_only_the_current_turn(hass, monkeypatch):
    agent = await _agent(
        hass,
        **{
            CONF_CONVERSATION_CONTINUITY: CONVERSATION_CONTINUITY_DEVICE,
            CONF_CONVERSATION_TIMEOUT_MINUTES: 20,
        },
    )
    sent = _provider(monkeypatch, agent, ["Opening reply", "New reply"])
    first = await _say(hass, agent, "Opening witness", device_id="kitchen")
    first_log = hass.data[DATA_CHAT_LOGS][first.conversation_id]
    if not hasattr(first_log, "turn_start"):
        pytest.skip("This Home Assistant version has no native turn trace boundary")
    _expire(hass)
    second = await _say(hass, agent, "Current witness", device_id="kitchen")
    assert _speech(second) == "New reply"
    log = hass.data[DATA_CHAT_LOGS][second.conversation_id]
    assert log is not first_log
    assert "Opening witness" in str(sent[1]["messages"])
    assert [(item.role, item.content) for item in log.content[log.turn_start :]] == [
        ("user", "Current witness"),
        ("assistant", "New reply"),
    ]


async def test_local_rule_bounds_native_history_and_preserves_archive(
    hass, monkeypatch
):
    owner = await hass.auth.async_create_user(
        "Local history owner", group_ids=["system-admin"]
    )
    agent = await _agent(hass, **{CONF_ARCHIVE_ENABLED: True})

    async def turn_off(_call):
        pass

    hass.services.async_register("light", "turn_off", turn_off)
    hass.states.async_set("light.bedroom", "on")
    await agent._request_rules.async_create(
        _rule(
            "local_action",
            {"actions": [_action("light.bedroom")], "success_response": "Done."},
            match_type="starts_with",
            phrase="Local witness",
        )
    )
    sent = _provider(monkeypatch, agent, [])
    chat_id = None
    for index in range(54):
        result = await conversation.async_converse(
            hass=hass,
            text=f"Local witness {index}",
            conversation_id=chat_id,
            context=Context(user_id=owner.id),
            language="en",
            agent_id=agent.entry.entry_id,
        )
        assert result.response.error_code is None, (index, result.response.as_dict())
        assert _speech(result) == "Done."
        assert chat_id is None or result.conversation_id == chat_id
        chat_id = result.conversation_id

    log = hass.data[DATA_CHAT_LOGS][chat_id]
    assert log.content[0].role == "system"
    assert [(item.role, item.content) for item in log.content[1:]] == [
        item
        for index in range(4, 54)
        for item in (("user", f"Local witness {index}"), ("assistant", "Done."))
    ]
    if hasattr(log, "turn_start"):
        assert log.turn_start == len(log.content) - 2
    assert agent._archive.stats()["turn_count"] == 54
    assert sent == []
