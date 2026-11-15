"""Pass native chat-session ownership on HA versions that support it."""

from collections.abc import Callable, Generator
from contextlib import AbstractContextManager, contextmanager
from inspect import signature

from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers.chat_session import (
    ChatSession,
    async_get_chat_session as _ha_session,
)

_native_session: Callable[..., AbstractContextManager[ChatSession]] = _ha_session
_HAS_CONTEXT = "context" in signature(_ha_session).parameters


@contextmanager
def _legacy_session(
    hass: HomeAssistant, conversation_id: str | None
) -> Generator[ChatSession]:
    """Let legacy Core persist/schedule a failed turn before propagating it.

    Older helpers perform teardown only after a normal return from yield.
    Retain native expiry of the actual session/history, rather than releasing
    ownership based on whether a ChatLog happens to be cached after failure.
    """
    failure: BaseException | None = None
    with _native_session(hass, conversation_id) as session:
        try:
            yield session
        except BaseException as err:
            failure = err
    if failure is not None:
        raise failure


def async_get_chat_session(
    hass: HomeAssistant, conversation_id: str | None = None, *, context: Context
) -> AbstractContextManager[ChatSession]:
    """Keep the stable direct path; forward the actual caller to newer Core."""
    if _HAS_CONTEXT:
        return _native_session(hass, conversation_id, context=context)
    return _legacy_session(hass, conversation_id)
