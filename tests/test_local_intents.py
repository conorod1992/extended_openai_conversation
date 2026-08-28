"""Tests for optional Home Assistant local intent routing."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

from homeassistant.components import conversation
from homeassistant.helpers import intent as ha_intent

from custom_components.extended_openai_conversation_responses.local_intents import (
    CONF_LOCAL_INTENT_DELAYED_COMMANDS_TO_AI,
    CONF_LOCAL_INTENT_EXCLUSIONS,
    CONF_LOCAL_INTENTS_ENABLED,
    async_try_handle_local_intent,
    conflicting_assist_pipelines,
    registered_intent_catalog,
    should_handle_locally,
)


def _recognize(intent_name: str, entities: dict[str, Any] | None = None):
    return cast(
        Any,
        SimpleNamespace(
            intent=SimpleNamespace(name=intent_name),
            entities=entities or {},
        ),
    )


def test_intent_filter_allows_unexcluded_intent() -> None:
    assert should_handle_locally(_recognize("HassTurnOff"), [], False)


def test_intent_filter_rejects_explicit_exclusion() -> None:
    assert not should_handle_locally(
        _recognize("HassBroadcast"), ["HassBroadcast"], False
    )


def test_delayed_command_can_be_sent_to_ai_without_redirecting_normal_timer() -> None:
    delayed = _recognize("HassStartTimer", {"conversation_command": object()})
    ordinary = _recognize("HassStartTimer", {"minutes": object()})

    assert not should_handle_locally(delayed, [], True)
    assert should_handle_locally(ordinary, [], True)


@pytest.mark.asyncio
async def test_disabled_local_handling_does_not_call_home_assistant(
    hass, monkeypatch: pytest.MonkeyPatch
) -> None:
    called = False

    async def fake_handle(*args, **kwargs):
        nonlocal called
        called = True
        return None

    monkeypatch.setattr(conversation, "async_handle_intents", fake_handle)
    result = await async_try_handle_local_intent(
        hass,
        cast(Any, SimpleNamespace()),
        cast(Any, SimpleNamespace()),
        {CONF_LOCAL_INTENTS_ENABLED: False},
        guest_active=False,
    )

    assert result is None
    assert not called


@pytest.mark.asyncio
async def test_guest_mode_keeps_existing_policy_path(
    hass, monkeypatch: pytest.MonkeyPatch
) -> None:
    called = False

    async def fake_handle(*args, **kwargs):
        nonlocal called
        called = True
        return None

    monkeypatch.setattr(conversation, "async_handle_intents", fake_handle)
    result = await async_try_handle_local_intent(
        hass,
        cast(Any, SimpleNamespace()),
        cast(Any, SimpleNamespace()),
        {CONF_LOCAL_INTENTS_ENABLED: True},
        guest_active=True,
    )

    assert result is None
    assert not called


@pytest.mark.asyncio
async def test_home_assistant_response_is_preserved_and_intent_is_reported(
    hass, monkeypatch: pytest.MonkeyPatch
) -> None:
    response = ha_intent.IntentResponse(language="en")
    response.async_set_speech("Done")

    async def fake_handle(hass, user_input, chat_log, *, intent_filter=None):
        assert intent_filter is not None
        assert intent_filter(_recognize("HassTurnOn"))
        return response

    monkeypatch.setattr(conversation, "async_handle_intents", fake_handle)
    result = await async_try_handle_local_intent(
        hass,
        cast(Any, SimpleNamespace()),
        cast(Any, SimpleNamespace()),
        {CONF_LOCAL_INTENTS_ENABLED: True},
        guest_active=False,
    )

    assert result is not None
    assert result.intent_name == "HassTurnOn"
    assert result.response is response


@pytest.mark.asyncio
async def test_excluded_match_falls_through(
    hass, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_handle(hass, user_input, chat_log, *, intent_filter=None):
        assert intent_filter is not None
        assert not intent_filter(_recognize("HassBroadcast"))
        return None

    monkeypatch.setattr(conversation, "async_handle_intents", fake_handle)
    result = await async_try_handle_local_intent(
        hass,
        cast(Any, SimpleNamespace()),
        cast(Any, SimpleNamespace()),
        {
            CONF_LOCAL_INTENTS_ENABLED: True,
            CONF_LOCAL_INTENT_EXCLUSIONS: ["HassBroadcast"],
        },
        guest_active=False,
    )

    assert result is None


def test_registered_intent_catalog_is_live_and_keeps_saved_missing_choices(
    hass, monkeypatch: pytest.MonkeyPatch
) -> None:
    handlers = [
        SimpleNamespace(intent_type="HassTurnOn"),
        SimpleNamespace(intent_type="HassNewFutureIntent"),
    ]
    monkeypatch.setattr(ha_intent, "async_get", lambda hass: handlers)

    catalog = registered_intent_catalog(hass, ["HassOldRemovedIntent"])
    by_name = {item["intent"]: item for item in catalog}

    assert by_name["HassTurnOn"]["available"] is True
    assert by_name["HassNewFutureIntent"]["available"] is True
    assert by_name["HassOldRemovedIntent"]["available"] is False
    assert by_name["HassNewFutureIntent"]["label"] == "New Future Intent"


def test_pipeline_conflicts_are_limited_to_this_agent(
    hass, monkeypatch: pytest.MonkeyPatch
) -> None:
    from custom_components.extended_openai_conversation_responses import local_intents

    monkeypatch.setattr(
        local_intents,
        "_conversation_entity_id",
        lambda hass, entry_id, subentry_id: "conversation.extended_openai",
    )
    monkeypatch.setattr(
        local_intents.assist_pipeline,
        "async_get_pipelines",
        lambda hass: [
            SimpleNamespace(
                id="one",
                name="Kitchen Assist",
                conversation_engine="conversation.extended_openai",
                prefer_local_intents=True,
            ),
            SimpleNamespace(
                id="two",
                name="Bedroom Assist",
                conversation_engine="conversation.extended_openai",
                prefer_local_intents=False,
            ),
            SimpleNamespace(
                id="three",
                name="Other agent",
                conversation_engine="conversation.home_assistant",
                prefer_local_intents=True,
            ),
        ],
    )

    assert conflicting_assist_pipelines(hass, "entry", "agent") == [
        {"id": "one", "name": "Kitchen Assist"}
    ]


@pytest.mark.asyncio
async def test_delayed_command_setting_is_applied_to_home_assistant_filter(
    hass, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_handle(hass, user_input, chat_log, *, intent_filter=None):
        assert intent_filter is not None
        assert not intent_filter(
            _recognize("HassStartTimer", {"conversation_command": object()})
        )
        assert intent_filter(_recognize("HassStartTimer", {"minutes": object()}))
        return None

    monkeypatch.setattr(conversation, "async_handle_intents", fake_handle)
    assert (
        await async_try_handle_local_intent(
            hass,
            cast(Any, SimpleNamespace()),
            cast(Any, SimpleNamespace()),
            {
                CONF_LOCAL_INTENTS_ENABLED: True,
                CONF_LOCAL_INTENT_DELAYED_COMMANDS_TO_AI: True,
            },
            guest_active=False,
        )
        is None
    )
