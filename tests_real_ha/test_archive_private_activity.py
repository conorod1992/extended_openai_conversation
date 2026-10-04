"""Private Archive activity through Home Assistant's conversation entry point."""

from datetime import timedelta

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
