"""Regression contracts for runtime hardening state and retry behavior."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import guest_mode


@pytest.fixture
def install_state_guard():
    """Exercise the initializer defined by the owning manager."""
    return guest_mode.GuestModeManager.async_initialize


def manager(hass, stored):
    """Return the minimum manager shape used by the hardened initializer."""
    return SimpleNamespace(
        hass=hass,
        _store=SimpleNamespace(async_load=AsyncMock(return_value=stored)),
        _schedule="unchanged",
        _initialized=False,
        _initialization_lock=asyncio.Lock(),
    )


@pytest.mark.parametrize(
    "stored",
    [
        {"schedule": {"source": "legacy-without-start"}},
        {"schedule": {"active_from": "not-a-timestamp"}},
    ],
    ids=["constructor-shape", "timestamp"],
)
async def test_malformed_persisted_state_is_ignored(
    hass, install_state_guard, caplog, stored
):
    subject = manager(hass, stored)

    await install_state_guard(subject)

    assert subject._schedule is None
    assert subject._initialized is True
    assert "Ignoring malformed Guest Mode state" in caplog.text


async def test_non_mapping_persisted_state_initializes_empty(hass, install_state_guard):
    subject = manager(hass, ["legacy", "payload"])

    await install_state_guard(subject)

    assert subject._schedule is None
    assert subject._initialized is True


async def test_storage_failure_stays_retryable(hass, install_state_guard):
    load = AsyncMock(side_effect=[OSError("store unavailable"), {}])
    subject = SimpleNamespace(
        hass=hass,
        _store=SimpleNamespace(async_load=load),
        _schedule=None,
        _initialized=False,
        _initialization_lock=asyncio.Lock(),
    )

    with pytest.raises(OSError, match="store unavailable"):
        await install_state_guard(subject)

    assert subject._initialized is False

    await install_state_guard(subject)

    assert subject._initialized is True
    assert subject._schedule is None
    assert load.await_count == 2
