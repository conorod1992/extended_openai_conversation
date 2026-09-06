"""Runtime normalization for Voice Identity source-device semantics."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import wraps
from typing import Any

_INSTALLED = False


@contextmanager
def _prefer_registry_device_source(user_input: Any) -> Iterator[None]:
    """Prefer HA's registry device ID while preserving satellite metadata afterwards.

    Home Assistant supplies both ``device_id`` and ``satellite_id`` for Assist
    satellite requests. Voice Identity mappings and device-scoped continuity are
    keyed by the device-registry ID, while ``satellite_id`` identifies the Assist
    satellite entity. The conversation engine historically preferred the latter.

    Temporarily hide only the satellite source when a registry device ID is also
    available so the existing resolver follows its normal ``device_id`` path. Keep
    the satellite fallback unchanged for callers that genuinely have no device ID.
    """
    device_id = getattr(user_input, "device_id", None)
    satellite_id = getattr(user_input, "satellite_id", None)
    if not device_id or not satellite_id:
        yield
        return

    user_input.satellite_id = None
    try:
        yield
    finally:
        user_input.satellite_id = satellite_id


def install_voice_identity_runtime() -> None:
    """Install registry-device precedence on the effective conversation entry point."""
    global _INSTALLED
    if _INSTALLED:
        return

    from . import conversation

    current = conversation.ExtendedOpenAIAgentEntity._async_process
    if getattr(current, "_extended_openai_voice_identity_device", False):
        _INSTALLED = True
        return

    @wraps(current)
    async def process_with_registry_device(self: Any, user_input: Any) -> Any:
        with _prefer_registry_device_source(user_input):
            return await current(self, user_input)

    process_with_registry_device._extended_openai_voice_identity_device = True  # type: ignore[attr-defined]
    conversation.ExtendedOpenAIAgentEntity._async_process = (  # type: ignore[method-assign]
        process_with_registry_device
    )
    _INSTALLED = True
