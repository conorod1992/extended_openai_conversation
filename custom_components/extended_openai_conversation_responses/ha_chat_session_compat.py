"""Pass native chat-session ownership on HA versions that support it."""

from collections.abc import Callable
from contextlib import AbstractContextManager
from inspect import signature

from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers.chat_session import (
    ChatSession,
    async_get_chat_session as _ha_session,
)

_native_session: Callable[..., AbstractContextManager[ChatSession]] = _ha_session
_HAS_CONTEXT = "context" in signature(_ha_session).parameters


def async_get_chat_session(
    hass: HomeAssistant, conversation_id: str | None = None, *, context: Context
) -> AbstractContextManager[ChatSession]:
    """Keep the stable direct path; forward the actual caller to newer Core."""
    if _HAS_CONTEXT:
        return _native_session(hass, conversation_id, context=context)
    return _native_session(hass, conversation_id)
