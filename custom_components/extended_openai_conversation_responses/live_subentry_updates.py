"""Mark subentry writes that the live agent can consume without a reload."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from .const import CONF_MAX_TOKENS, CONF_TEMPERATURE, CONF_TOP_P

# These values are read from subentry.data at each provider request boundary.
# Expand only after covering any setup-owned effects for the additional fields.
_LIVE_REQUEST_FIELDS = frozenset({CONF_MAX_TOKENS, CONF_TEMPERATURE, CONF_TOP_P})

_LIVE_SUBENTRY_UPDATE: ContextVar[bool] = ContextVar(
    "extended_openai_live_subentry_update", default=False
)


@contextmanager
def live_subentry_update() -> Iterator[None]:
    """Identify one persisted update that does not require runtime replacement.

    Home Assistant copies the current context when it schedules config-entry update
    listeners. The listener can therefore distinguish this exact write from entry
    credential/options changes without process-global flags or timing assumptions.
    """
    token = _LIVE_SUBENTRY_UPDATE.set(True)
    try:
        yield
    finally:
        _LIVE_SUBENTRY_UPDATE.reset(token)


def is_live_subentry_update() -> bool:
    """Return whether the current update-listener task belongs to a live write."""
    return _LIVE_SUBENTRY_UPDATE.get()


def update_live_subentry(hass: Any, entry: Any, subentry: Any, **changes: Any) -> None:
    """Persist one subentry change consumed by the live request boundary."""
    with live_subentry_update():
        hass.config_entries.async_update_subentry(entry, subentry, **changes)


def configuration_supports_live_update(
    previous: Mapping[str, Any], candidate: Mapping[str, Any], *, title_changed: bool
) -> bool:
    """Allow only reviewed effective changes, including normalization side effects."""
    from .agent_config import agent_config_defaults

    if title_changed:
        return False
    defaults = agent_config_defaults()
    missing = object()
    changed = {
        key
        for key in previous.keys() | candidate.keys()
        if previous.get(key, defaults.get(key, missing))
        != candidate.get(key, defaults.get(key, missing))
    }
    return changed <= _LIVE_REQUEST_FIELDS
