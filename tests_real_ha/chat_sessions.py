"""Create unowned native history for fixtures across supported HA versions.

Request ownership is exercised by each test's real Assist/AI Task context. The
history fixture itself does not impersonate an authenticated caller.
"""

from inspect import signature

from homeassistant.helpers.chat_session import async_get_chat_session as _native_session

_HAS_CONTEXT = "context" in signature(_native_session).parameters


def async_get_chat_session(hass, conversation_id=None):
    if _HAS_CONTEXT:
        return _native_session(hass, conversation_id, context=None)
    return _native_session(hass, conversation_id)
