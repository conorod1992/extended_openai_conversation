"""Physical request origin retained across HA's reduced LLMContext boundary."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

_REQUEST_SATELLITE: ContextVar[str | None] = ContextVar(
    "extended_openai_request_satellite", default=None
)


@contextmanager
def bind_request_satellite(satellite_id: str | None) -> Iterator[None]:
    """Bind each invocation, including absent origins, and restore its caller."""
    token = _REQUEST_SATELLITE.set(satellite_id)
    try:
        yield
    finally:
        _REQUEST_SATELLITE.reset(token)


def get_request_satellite() -> str | None:
    """Return the trusted invocation's satellite, never model-supplied arguments."""
    return _REQUEST_SATELLITE.get()


def registered_satellite(
    hass: HomeAssistant, satellite_id: str | None
) -> er.RegistryEntry | None:
    """Read the current registry binding for a trusted Assist satellite ID."""
    if not isinstance(satellite_id, str) or not satellite_id.startswith(
        "assist_satellite."
    ):
        return None
    try:
        return er.async_get(hass).async_get(satellite_id)
    except AttributeError, KeyError, TypeError:
        return None


def satellite_registry_identity(
    hass: HomeAssistant, satellite_id: str | None
) -> tuple[str, str] | None:
    """Return the integration and stable unique ID rather than the entity name."""
    entry = registered_satellite(hass, satellite_id)
    if entry is None:
        return None
    platform = getattr(entry, "platform", None)
    unique_id = getattr(entry, "unique_id", None)
    if (
        isinstance(platform, str)
        and platform
        and isinstance(unique_id, str)
        and unique_id
    ):
        return platform, unique_id
    return None
