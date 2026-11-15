"""Native HA expiry/replacement must not orphan invocation ownership claims."""

import asyncio
from contextlib import contextmanager
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import (
    conversation as agent_module,
    conversation_id_ownership as ownership,
)
from custom_components.extended_openai_conversation_responses.ha_chat_session_compat import (
    async_get_chat_session,
)
from custom_components.extended_openai_conversation_responses.scope import user_scope
from homeassistant.components import conversation
from homeassistant.components.conversation.chat_log import DATA_CHAT_LOGS
from homeassistant.core import Context
from homeassistant.exceptions import Unauthorized
from homeassistant.helpers.chat_session import (
    DATA_CHAT_SESSION,
    DATA_CHAT_SESSION_CLEANUP,
)
from homeassistant.util import dt as dt_util
from tests_real_ha.test_cross_feature_acceptance import _agent, _provider, _say


def _expire(hass):
    for session in hass.data.get(DATA_CHAT_SESSION, {}).values():
        session.last_updated = dt_util.utcnow() - timedelta(minutes=10)
    cleanup = hass.data[DATA_CHAT_SESSION_CLEANUP]
    if cleanup.unsub:
        cleanup.unsub()
    cleanup._cleanup(dt_util.utcnow())
    assert not hass.data[DATA_CHAT_SESSION]
    assert not hass.data.get(DATA_CHAT_LOGS)
    assert not hass.data.get(ownership._CLEANUP_REGISTRATIONS)


def _assert_released(agent):
    assert not agent._continuity._ha_default_claims
    assert not agent._continuity._ha_default_locks


async def test_stale_ulid_repeated_expiry_resume_retires_unused_claims(
    hass, monkeypatch
):
    agent = await _agent(hass)
    _provider(monkeypatch, agent, ["Ready."] * 4)
    previous = None
    for _ in range(4):
        result = await _say(hass, agent, "Status please", previous)
        assert result.response.error_code is None
        assert result.conversation_id != previous
        actual = result.conversation_id
        assert set(hass.data[ownership._CONVERSATION_ID_OWNERS]) == {actual}
        _assert_released(agent)
        _expire(hass)
        assert not hass.data[ownership._CONVERSATION_ID_OWNERS]
        previous = actual


@pytest.mark.parametrize("failure", [RuntimeError, asyncio.CancelledError])
async def test_replacement_history_stays_owned_after_later_failure(
    hass, monkeypatch, failure
):
    agent = await _agent(hass)
    _provider(monkeypatch, agent, ["Ready."])
    first = await _say(hass, agent, "Status please")
    _expire(hass)
    monkeypatch.setattr(
        agent,
        "_async_begin_archive_session",
        AsyncMock(side_effect=failure("after allocation")),
    )
    with pytest.raises(failure):
        await _say(hass, agent, "Retry status", first.conversation_id)
    actual_ids = set(hass.data[DATA_CHAT_SESSION])
    assert len(actual_ids) == 1 and first.conversation_id not in actual_ids
    assert set(hass.data[ownership._CONVERSATION_ID_OWNERS]) == actual_ids
    assert set(hass.data[ownership._CLEANUP_REGISTRATIONS]) == actual_ids
    _assert_released(agent)
    _expire(hass)
    assert not hass.data[ownership._CONVERSATION_ID_OWNERS]


def _input(agent, conversation_id, *, user_id=None):
    return conversation.ConversationInput(
        text="Status please",
        context=Context(user_id=user_id),
        conversation_id=conversation_id,
        device_id=None,
        satellite_id=None,
        language="en",
        agent_id=agent.entity_id,
    )


@pytest.mark.parametrize("failure", [RuntimeError, asyncio.CancelledError])
async def test_failure_before_session_attachment_releases_new_returned_claim(
    hass, monkeypatch, failure
):
    agent = await _agent(hass)
    owner = ("other-agent", "user:other")
    hass.data[ownership._CONVERSATION_ID_OWNERS] = {"foreign": owner}
    invoked = []

    @contextmanager
    def rejected(_hass, conversation_id, **kwargs):
        invoked.append(conversation_id)
        raise failure("before attachment")
        yield  # pragma: no cover

    monkeypatch.setattr(agent_module, "async_get_chat_session", rejected)
    with pytest.raises(failure):
        await agent.async_process(_input(agent, "foreign"))
    assert len(invoked) == 1 and invoked[0] != "foreign"
    assert hass.data[ownership._CONVERSATION_ID_OWNERS] == {"foreign": owner}
    _assert_released(agent)


async def test_native_authorization_failure_retires_only_new_claim(hass, monkeypatch):
    agent = await _agent(hass)
    with async_get_chat_session(
        hass, "native-private", context=Context(user_id="native-owner")
    ):
        pass
    with pytest.raises(Unauthorized):
        await agent.async_process(
            _input(agent, "native-private", user_id="different-owner")
        )
    assert not hass.data[ownership._CONVERSATION_ID_OWNERS]
    assert not hass.data.get(ownership._CLEANUP_REGISTRATIONS)
    assert hass.data[DATA_CHAT_SESSION]["native-private"].user_id == "native-owner"
    _assert_released(agent)
    _expire(hass)


@pytest.mark.parametrize("failure", [RuntimeError, asyncio.CancelledError])
async def test_failed_chatlog_admission_keeps_actual_session_protected(
    hass, monkeypatch, failure
):
    agent = await _agent(hass)
    _provider(monkeypatch, agent, ["Ready."])
    first = await _say(hass, agent, "Status please")
    _expire(hass)

    @contextmanager
    def rejected(*args, **kwargs):
        raise failure("ChatLog admission")
        yield  # pragma: no cover

    monkeypatch.setattr(agent_module, "async_get_chat_log", rejected)
    with pytest.raises(failure):
        await agent.async_process(_input(agent, first.conversation_id))
    actual_ids = set(hass.data[DATA_CHAT_SESSION])
    assert len(actual_ids) == 1 and first.conversation_id not in actual_ids
    assert set(hass.data[ownership._CONVERSATION_ID_OWNERS]) == actual_ids
    assert set(hass.data[ownership._CLEANUP_REGISTRATIONS]) == actual_ids
    _assert_released(agent)
    _expire(hass)
    assert not hass.data[ownership._CONVERSATION_ID_OWNERS]


@pytest.mark.parametrize("failure", [RuntimeError, asyncio.CancelledError])
@pytest.mark.parametrize("after_original_release", [False, True])
async def test_continuity_replacement_failure_releases_both_claims(
    hass, monkeypatch, failure, after_original_release
):
    agent = await _agent(hass)
    owner = ("other-agent", "user:other")
    hass.data[ownership._CONVERSATION_ID_OWNERS] = {"foreign": owner}
    if after_original_release:
        original_claim = agent._continuity._async_claim_ha_default_conversation

        async def fail_replacement_claim(conversation_id):
            if conversation_id != "foreign":
                raise failure("replacement claim")
            return await original_claim(conversation_id)

        monkeypatch.setattr(
            agent._continuity,
            "_async_claim_ha_default_conversation",
            fail_replacement_claim,
        )
    else:
        monkeypatch.setattr(
            agent._continuity,
            "async_replace_conversation_id",
            AsyncMock(side_effect=failure("replacement")),
        )
    with pytest.raises(failure):
        await agent.async_process(_input(agent, "foreign"))
    assert hass.data[ownership._CONVERSATION_ID_OWNERS] == {"foreign": owner}
    _assert_released(agent)


async def test_reused_claim_without_native_cache_is_preserved_on_failure(
    hass, monkeypatch
):
    agent = await _agent(hass)
    user = await hass.auth.async_create_user("Retained owner")
    ownership.claim_conversation_id(
        hass,
        agent.subentry.subentry_id,
        user_scope(user.id, source="test"),
        "retained",
        guest_active=False,
    )
    before = dict(hass.data[ownership._CONVERSATION_ID_OWNERS])
    monkeypatch.setattr(
        agent,
        "_async_process_claimed",
        AsyncMock(side_effect=RuntimeError("before native admission")),
    )
    with pytest.raises(RuntimeError):
        await agent.async_process(_input(agent, "retained", user_id=user.id))
    assert hass.data[ownership._CONVERSATION_ID_OWNERS] == before
    _assert_released(agent)


@pytest.mark.parametrize("stable", [False, True])
async def test_reused_live_history_survives_failed_turn(hass, monkeypatch, stable):
    agent = await _agent(hass)
    _provider(monkeypatch, agent, ["Ready."])
    first = await _say(
        hass, agent, "Original private history", "custom-stable" if stable else None
    )
    owner = hass.data[ownership._CONVERSATION_ID_OWNERS][first.conversation_id]
    monkeypatch.setattr(
        agent,
        "_async_begin_archive_session",
        AsyncMock(side_effect=RuntimeError("later failure")),
    )
    with pytest.raises(RuntimeError):
        await _say(hass, agent, "Failed turn", first.conversation_id)
    assert hass.data[ownership._CONVERSATION_ID_OWNERS] == {
        first.conversation_id: owner
    }
    assert any(
        item.content == "Original private history"
        for item in hass.data[DATA_CHAT_LOGS][first.conversation_id].content
        if isinstance(item, conversation.UserContent)
    )
    _assert_released(agent)
    _expire(hass)
    assert not hass.data[ownership._CONVERSATION_ID_OWNERS]
