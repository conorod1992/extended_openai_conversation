"""Private Archive retention survives selective restore and trusted rollback."""

import json

import pytest
from pytest_homeassistant_custom_component.common import MockUser

from custom_components.extended_openai_conversation_responses import backup, transfer
from custom_components.extended_openai_conversation_responses.const import (
    CONF_ARCHIVE_ENABLED,
    CONF_CONVERSATION_CONTINUITY,
    CONVERSATION_CONTINUITY_DEVICE,
    CONVERSATION_CONTINUITY_HA_DEFAULT,
    CONVERSATION_CONTINUITY_USER,
)
from homeassistant.components import conversation
from homeassistant.core import Context
from tests_real_ha.test_cross_feature_acceptance import _agent, _provider


@pytest.mark.parametrize(
    "mode",
    [
        CONVERSATION_CONTINUITY_HA_DEFAULT,
        CONVERSATION_CONTINUITY_USER,
        CONVERSATION_CONTINUITY_DEVICE,
    ],
)
@pytest.mark.parametrize("rollback", [False, True])
async def test_private_followup_after_knowledge_restore(
    hass, monkeypatch, mode, rollback
):
    user = MockUser(id="restore-private-owner")
    user.add_to_hass(hass)
    agent = await _agent(
        hass, **{CONF_ARCHIVE_ENABLED: True, CONF_CONVERSATION_CONTINUITY: mode}
    )
    sent = _provider(
        monkeypatch, agent, ["Before restore reply", "Private after restore reply"]
    )

    async def say(text, conversation_id=None):
        return await conversation.async_converse(
            hass=hass,
            text=text,
            conversation_id=conversation_id,
            context=Context(user_id=user.id),
            device_id="restore-device",
            language="en",
            agent_id=agent.entry.entry_id,
        )

    first = await say("Before restore content")
    assert first.response.error_code is None
    session = next(iter(agent._archive._sessions.values()))
    await agent._archive.async_make_private(session.session_id)
    document = transfer._new_transfer_document(agent.entry, agent.subentry, "custom")
    document["sections"][transfer.SECTION_KNOWLEDGE] = {"sources": []}
    if rollback:
        original = agent._knowledge.async_replace_backup
        calls = 0

        async def fail_first(records):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("injected Knowledge restore failure")
            await original(records)

        monkeypatch.setattr(agent._knowledge, "async_replace_backup", fail_first)
        with pytest.raises(
            backup.BackupError, match="previous agent state was recovered"
        ):
            await transfer.async_restore_transfer(
                hass, agent.entry, agent.subentry, document
            )
        assert calls == 2
    else:
        await transfer.async_restore_transfer(
            hass, agent.entry, agent.subentry, document
        )
    followup = await say("PRIVATE AFTER RESTORE", first.conversation_id)
    assert followup.response.error_code is None
    assert followup.conversation_id == first.conversation_id
    assert "PRIVATE AFTER RESTORE" not in json.dumps(
        await agent._archive.async_backup_data()
    )
    assert agent._archive._sessions[session.session_id].retention_state == "private"
    if mode != CONVERSATION_CONTINUITY_HA_DEFAULT:
        # Reusing a privacy identity must not resurrect provider context in Core's log.
        assert "Before restore content" not in json.dumps(sent[-1])
        assert "Before restore reply" not in json.dumps(sent[-1])
