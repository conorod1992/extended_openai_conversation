"""Private Archive activity through Home Assistant's conversation entry point."""

from datetime import timedelta

import pytest
from pytest_homeassistant_custom_component.common import MockUser

from custom_components.extended_openai_conversation_responses.const import (
    CONF_ARCHIVE_ENABLED,
    CONF_ARCHIVE_SESSION_TIMEOUT_MINUTES,
    CONF_SHARED_ARCHIVE_ENABLED,
)
from homeassistant.components import conversation
from homeassistant.core import Context
from homeassistant.util import dt as dt_util
from tests_real_ha.test_cross_feature_acceptance import _agent, _provider, _speech
from tests_stress.test_os_storage_faults import real_store_io  # noqa: F401


async def test_continuing_private_conversation_never_resumes_retention(
    hass, monkeypatch
):
    agent = await _agent(
        hass,
        **{
            CONF_ARCHIVE_ENABLED: True,
            CONF_SHARED_ARCHIVE_ENABLED: True,
            CONF_ARCHIVE_SESSION_TIMEOUT_MINUTES: 30,
        },
    )
    _provider(monkeypatch, agent, ["Reply"] * 13)
    user = MockUser(id="archive-user", name="Archive User")
    user.add_to_hass(hass)

    async def say(text, conversation_id=None):
        return await conversation.async_converse(
            hass=hass,
            text=text,
            conversation_id=conversation_id,
            context=Context(user_id=user.id),
            language="en",
            agent_id=agent.entry.entry_id,
        )

    first = await say("Start this conversation")
    assert _speech(first) == "Reply"
    archive = agent._archive
    session = next(iter(archive._sessions.values()))
    assert session.turn_count == 1
    await archive.async_make_private(session.session_id)
    now = dt_util.utcnow()
    monkeypatch.setattr(dt_util, "utcnow", lambda: now)
    for _ in range(12):
        now += timedelta(minutes=10)
        result = await say("Private content", first.conversation_id)
        assert _speech(result) == "Reply"
        assert len(archive._sessions) == 1
        current = archive._sessions[session.session_id]
        assert current.retention_state == "private"
        assert current.turn_count == 0
    assert archive.stats()["turn_count"] == 0


@pytest.mark.usefixtures("real_store_io")
async def test_public_archive_privacy_resume_delete_survives_reload_and_preserves_other_session(
    hass, monkeypatch
):
    from custom_components.extended_openai_conversation_responses.conversation_archive import (
        ConversationArchive,
        HomeAssistantArchiveStorage,
    )
    from tests_real_ha.test_conversation_runtime_lifecycle import _tool

    user = MockUser(id="archive-owner", name="Archive Owner")
    user.add_to_hass(hass)
    agent = await _agent(hass, **{CONF_ARCHIVE_ENABLED: True})
    sent = _provider(
        monkeypatch,
        agent,
        [
            "Retained sibling.",
            "Initial retained.",
            _tool("conversation_private"),
            "Private reply.",
            "Private followup.",
            _tool("conversation_resume_saving"),
            "Resumed reply.",
            "Retained after resume.",
            _tool("conversation_delete_current"),
            "Deleted reply.",
            "New session reply.",
        ],
    )

    async def say(text, conversation_id=None):
        return await conversation.async_converse(
            hass=hass,
            text=text,
            conversation_id=conversation_id,
            context=Context(user_id=user.id),
            language="en",
            agent_id=agent.entry.entry_id,
        )

    sibling = await say("Separate retained conversation")
    assert _speech(sibling) == "Retained sibling."
    archive = agent._archive
    sibling_id = next(iter(archive._sessions))
    first = await say("Initial private candidate")
    assert _speech(first) == "Initial retained."
    original_id = next(key for key in archive._sessions if key != sibling_id)
    assert (
        _speech(await say("Make this private", first.conversation_id))
        == "Private reply."
    )
    assert (
        _speech(await say("Secret followup", first.conversation_id))
        == "Private followup."
    )
    private_backup = await archive.async_backup_data()
    assert archive._sessions[original_id].retention_state == "private"
    assert archive._sessions[original_id].turn_count == 0
    assert original_id not in archive._turns or not archive._turns[original_id]
    assert "Initial private candidate" not in str(private_backup)
    assert "Secret followup" not in str(private_backup)
    assert archive._sessions[sibling_id].turn_count == 1

    assert (
        _speech(await say("Resume saving", first.conversation_id)) == "Resumed reply."
    )
    resumed_id = next(
        key for key in archive._sessions if key not in {original_id, sibling_id}
    )
    assert archive._sessions[resumed_id].retention_state == "retained"
    assert archive._sessions[original_id].retention_state == "private"
    assert (
        _speech(await say("Retain new content", first.conversation_id))
        == "Retained after resume."
    )
    resumed_backup = await archive.async_backup_data()
    assert "Private reply." not in str(resumed_backup)
    assert "Secret followup" not in str(resumed_backup)
    assert "Retained after resume." in str(resumed_backup)
    assert (
        _speech(await say("Delete current", first.conversation_id)) == "Deleted reply."
    )
    assert resumed_id not in archive._sessions
    assert resumed_id not in archive._turns

    restored = ConversationArchive(
        HomeAssistantArchiveStorage(
            hass, agent.entry.entry_id, agent.subentry.subentry_id
        ),
        agent.subentry.subentry_id,
    )
    await restored.async_initialize()
    assert await restored.async_backup_data() == await archive.async_backup_data()
    assert resumed_id not in restored._sessions
    assert restored._sessions[sibling_id].turn_count == 1
    assert "Retained after resume." not in str(await restored.async_backup_data())
    assert (
        _speech(await say("Continue after deletion", first.conversation_id))
        == "New session reply."
    )
    assert resumed_id not in archive._sessions
    assert archive._sessions[sibling_id].turn_count == 1
    assert len(sent) == 11
