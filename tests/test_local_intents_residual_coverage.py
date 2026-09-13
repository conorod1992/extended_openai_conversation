"""Focused residual coverage for local intent routing."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

from homeassistant.components import conversation
from homeassistant.helpers import intent as ha_intent

from custom_components.extended_openai_conversation_responses import local_intents


@pytest.mark.asyncio
async def test_resolved_targeted_broadcast_short_circuits_ha_intent_engine(
    hass, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A resolved targeted broadcast must not fall through to HassBroadcast."""
    response = ha_intent.IntentResponse(language="en-IE")
    response.async_set_speech("Broadcast queued.")
    targeted = local_intents.LocalIntentResult(
        response=response,
        intent_name="ExtendedBroadcast",
    )
    handle_calls = 0

    async def fake_targeted(_hass: Any, _user_input: Any):
        return targeted

    async def fake_handle(*_args: Any, **_kwargs: Any):
        nonlocal handle_calls
        handle_calls += 1
        raise AssertionError("resolved targeted broadcast fell through to HA intents")

    monkeypatch.setattr(local_intents, "_async_try_targeted_broadcast", fake_targeted)
    monkeypatch.setattr(conversation, "async_handle_intents", fake_handle)

    result = await local_intents.async_try_handle_local_intent(
        hass,
        cast(
            Any,
            SimpleNamespace(
                text="Broadcast to the kitchen that dinner is ready",
                language="en-IE",
            ),
        ),
        cast(Any, SimpleNamespace()),
        {local_intents.CONF_LOCAL_INTENTS_ENABLED: True},
        guest_active=False,
    )

    assert result is targeted
    assert result.intent_name == "ExtendedBroadcast"
    assert result.response is response
    assert handle_calls == 0
