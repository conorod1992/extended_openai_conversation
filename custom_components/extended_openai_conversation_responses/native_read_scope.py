"""Resolve a native read's current caller scope at its execution boundary."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from homeassistant.core import HomeAssistant

EntityScope = list[dict[str, Any]]
_RESOLVER: ContextVar[Callable[[], EntityScope] | None] = ContextVar(
    "extended_openai_native_read_scope", default=None
)


@contextmanager
def native_read_scope(resolver: Callable[[], EntityScope]) -> Iterator[None]:
    """Carry the agent's live Guest/HA read policy through nested backends."""
    token = _RESOLVER.set(resolver)
    try:
        yield
    finally:
        _RESOLVER.reset(token)


def current_native_read_entities(
    hass: HomeAssistant, admitted: EntityScope
) -> EntityScope:
    """Narrow the admitted scope with current visibility; never widen a caller."""
    resolver = _RESOLVER.get()
    if resolver is not None:
        current = resolver()
    elif isinstance(hass, HomeAssistant):
        from .helpers import get_exposed_entities

        current = get_exposed_entities(hass)
    else:
        # Lightweight backend doubles have no real Assist exposure registry.
        return admitted
    allowed = {entity["entity_id"] for entity in current}
    return [entity for entity in admitted if entity["entity_id"] in allowed]
