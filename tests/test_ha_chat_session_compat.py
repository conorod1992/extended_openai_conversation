"""Native session ownership is explicit without breaking older HA versions."""

import asyncio
from contextlib import contextmanager

import pytest

from custom_components.extended_openai_conversation_responses import (
    ha_chat_session_compat as compat,
)
from homeassistant.core import Context
from homeassistant.exceptions import Unauthorized


@pytest.mark.parametrize("supports_context", [False, True])
@pytest.mark.parametrize("reject", [False, True])
def test_native_session_forwards_identity_and_preserves_outcome(
    monkeypatch, supports_context, reject
):
    hass = object()
    context = Context(user_id="caller")
    session = object()
    calls = []

    @contextmanager
    def native(*args, **kwargs):
        calls.append((args, kwargs))
        if reject:
            raise Unauthorized(context=context)
        yield session

    monkeypatch.setattr(compat, "_HAS_CONTEXT", supports_context)
    monkeypatch.setattr(compat, "_native_session", native)
    if reject:
        with pytest.raises(Unauthorized) as error:
            with compat.async_get_chat_session(hass, "retained-id", context=context):
                pytest.fail("An unauthorized native session must never be entered")
        assert error.value.context is context
    else:
        with compat.async_get_chat_session(
            hass, "retained-id", context=context
        ) as value:
            assert value is session
    assert calls == [
        ((hass, "retained-id"), {"context": context} if supports_context else {})
    ]


@pytest.mark.parametrize("failure_type", [RuntimeError, asyncio.CancelledError])
def test_legacy_failed_body_finishes_native_session_before_propagating(
    monkeypatch, failure_type
):
    finished = []
    error = failure_type("processing failed")
    session = object()

    @contextmanager
    def legacy(*args):
        yield session
        finished.append(session)

    monkeypatch.setattr(compat, "_HAS_CONTEXT", False)
    monkeypatch.setattr(compat, "_native_session", legacy)
    with pytest.raises(failure_type) as caught:
        with compat.async_get_chat_session(object(), "id", context=Context()) as value:
            assert value is session
            raise error
    assert caught.value is error
    assert finished == [session]
