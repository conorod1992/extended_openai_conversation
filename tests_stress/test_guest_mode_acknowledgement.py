"""Guest policy agrees with real Store replacements even when acknowledgement fails."""

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path

import atomicwrites
import pytest

from custom_components.extended_openai_conversation_responses.guest_mode import (
    GuestModeManager,
    resolve_guest_policy,
)
from homeassistant.exceptions import HomeAssistantError
from tests_stress.conftest import record
from tests_stress.test_os_storage_faults import real_store_io  # noqa: F401


@pytest.mark.parametrize("initial_active", [False, True])
@pytest.mark.parametrize("readable", [False, True])
@pytest.mark.usefixtures("real_store_io")
async def test_post_replace_guest_acknowledgement_reconciles_effective_policy(
    hass, monkeypatch, stress_trace, initial_active, readable
):
    manager = GuestModeManager(hass, "ack-entry", "ack-agent")
    await manager.async_initialize()
    if initial_active:
        await manager.async_update_trusted(active_from="2026-01-01T00:00:00+00:00")
    else:
        await manager.async_disable_trusted()
    assert manager.is_active() is initial_active
    replace = atomicwrites.replace_atomic
    replacements = []

    def replace_then_fail(source, destination):
        result = replace(source, destination)
        if Path(destination) == Path(manager._store.path):
            replacements.append(Path(destination).read_bytes())
            raise OSError("post-replacement acknowledgement failed")
        return result

    with monkeypatch.context() as fault:
        fault.setattr(atomicwrites, "replace_atomic", replace_then_fail)
        if not readable:

            async def cannot_reconcile():
                raise OSError("authoritative Store unreadable")

            fault.setattr(manager._store, "async_load", cannot_reconcile)
        with pytest.raises(OSError, match="Private storage write failed"):
            if initial_active:
                await manager.async_disable_trusted()
            else:
                await manager.async_update_trusted(
                    active_from="2026-01-01T00:00:00+00:00"
                )
        assert len(replacements) == 1
        if not readable:
            with pytest.raises(HomeAssistantError, match="storage recovery"):
                resolve_guest_policy(hass, {}, manager)
            assert manager._initialized is False
            with pytest.raises(HomeAssistantError, match="storage recovery"):
                await manager.async_backup_data()
            with pytest.raises(HomeAssistantError, match="storage recovery"):
                await manager.async_restrict(make_indefinite=True)
    expected_active = not initial_active
    disk = json.loads(Path(manager._store.path).read_text())["data"]
    assert (disk["schedule"] is not None) is expected_active
    fresh = GuestModeManager(hass, "ack-entry", "ack-agent")
    await fresh.async_initialize()
    if not readable:
        await manager.async_initialize()
    assert manager.schedule == fresh.schedule
    assert disk == {"schedule": asdict(fresh.schedule) if fresh.schedule else None}
    for current in (manager, fresh):
        assert current.is_active() is expected_active
        policy = resolve_guest_policy(hass, {}, current)
        assert policy.guest_active is expected_active
        assert policy.private_capabilities is (not expected_active)
        assert policy.personal_memory_write is (not expected_active)
    # A later trusted change still persists and survives another fresh load.
    await manager.async_disable_trusted()
    restarted = GuestModeManager(hass, "ack-entry", "ack-agent")
    await restarted.async_initialize()
    assert not restarted.is_active()
    record(
        stress_trace,
        "summary",
        guest_ambiguous_ack_cases=1,
        initial_active=initial_active,
        readable=readable,
        layer="genuine-ha-store-policy",
    )
