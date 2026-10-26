"""Physical request origin retained across HA's reduced LLMContext boundary."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

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
