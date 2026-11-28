"""Mark subentry writes that the live agent can consume without a reload."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from .const import CONF_MAX_TOKENS, CONF_TEMPERATURE, CONF_TOP_P, USAGE_RETENTION_FIELDS

# These values are read from subentry.data at each provider request boundary.
# Expand only after covering any setup-owned effects for the additional fields.
_LIVE_REQUEST_FIELDS = frozenset({CONF_MAX_TOKENS, CONF_TEMPERATURE, CONF_TOP_P})
_TYPE_SENSITIVE_FIELDS = _LIVE_REQUEST_FIELDS.union(USAGE_RETENTION_FIELDS)

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


def _same_numeric_types(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return all(
        type(left.get(key)) is type(right.get(key)) for key in _TYPE_SENSITIVE_FIELDS
    )


def persisted_configuration_matches(
    current: object, expected: Mapping[str, Any]
) -> bool:
    """Distinguish equal-valued numeric types during write verification."""
    return (
        isinstance(current, Mapping)
        and current == expected
        and _same_numeric_types(current, expected)
    )


class _TypeAwareConfiguration(dict[str, Any]):
    """Give Core a type-aware equality check for an otherwise equal config write."""

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Mapping):
            return NotImplemented
        return _same_numeric_types(self, other) and super().__eq__(dict(other))

    def __ne__(self, other: object) -> bool:
        if not isinstance(other, Mapping):
            return NotImplemented
        return not self == other


def write_configuration_subentry(
    hass: Any, entry: Any, subentry: Any, **changes: Any
) -> None:
    """Write through Core, including repairs to equal-valued numeric fields."""
    if (
        (data := changes.get("data")) is not None
        and subentry.data == data
        and not _same_numeric_types(subentry.data, data)
    ):
        changes["data"] = _TypeAwareConfiguration(data)
    hass.config_entries.async_update_subentry(entry, subentry, **changes)


def update_live_subentry(hass: Any, entry: Any, subentry: Any, **changes: Any) -> None:
    """Persist one subentry change consumed by the live request boundary."""
    with live_subentry_update():
        write_configuration_subentry(hass, entry, subentry, **changes)


def configuration_supports_live_update(
    previous: Mapping[str, Any], candidate: Mapping[str, Any], *, title_changed: bool
) -> bool:
    """Allow only reviewed effective changes, including normalization side effects."""
    from .agent_config import agent_config_defaults

    if title_changed:
        return False
    defaults = agent_config_defaults()
    missing = object()
    for key in (previous.keys() | candidate.keys()) - _LIVE_REQUEST_FIELDS:
        default = defaults.get(key, missing)
        old, new = previous.get(key, default), candidate.get(key, default)
        if old != new or (key in USAGE_RETENTION_FIELDS and type(old) is not type(new)):
            return False
    return True


def update_configuration_subentry(
    hass: Any,
    entry: Any,
    subentry: Any,
    *,
    data: Mapping[str, Any],
    title: str | None = None,
) -> bool:
    """Persist configuration with the same reload contract for every save route."""
    live = configuration_supports_live_update(
        subentry.data, data, title_changed=title is not None and title != subentry.title
    )
    changes: dict[str, Any] = {"data": data}
    if title is not None:
        changes["title"] = title
    if live:
        update_live_subentry(hass, entry, subentry, **changes)
    else:
        write_configuration_subentry(hass, entry, subentry, **changes)
    return live
