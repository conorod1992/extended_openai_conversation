"""Regression contracts for runtime hardening state and retry behavior."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import guest_mode
from custom_components.extended_openai_conversation_responses import runtime_hardening as runtime


@pytest.fixture
def install_state_guard(monkeypatch):
    """Install the state initializer guard from a clean unwrapped baseline."""

    async def original_initialize(_manager):
        raise AssertionError("guard did not replace the original initializer")

    monkeypatch.setattr(
        guest_mode.GuestModeManager, "async_initialize", original_initialize
    )
    runtime._install_guest_mode_hardening()
    return guest_mode.GuestModeManager.async_initialize


def manager(hass, stored):
    """Return the minimum manager shape used by the hardened initializer."""
    return SimpleNamespace(
        hass=hass,
        _store=SimpleNamespace(async_load=AsyncMock(return_value=stored)),
        _schedule="unchanged",
        _initialized=False,
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
    )

    with pytest.raises(OSError, match="store unavailable"):
        await install_state_guard(subject)

    assert subject._initialized is False

    await install_state_guard(subject)

    assert subject._initialized is True
    assert subject._schedule is None
    assert load.await_count == 2


def test_state_guard_installation_is_idempotent(monkeypatch):
    async def original_initialize(_manager):
        return None

    monkeypatch.setattr(
        guest_mode.GuestModeManager, "async_initialize", original_initialize
    )

    runtime._install_guest_mode_hardening()
    installed = guest_mode.GuestModeManager.async_initialize
    runtime._install_guest_mode_hardening()

    assert guest_mode.GuestModeManager.async_initialize is installed
    assert installed is not original_initialize
    assert installed._extended_openai_guest_guard is True
