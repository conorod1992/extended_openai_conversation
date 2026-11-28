"""Real HA state, service and disk boundaries for offline Quiet Hours controls."""

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path

import pytest

from custom_components.extended_openai_conversation_responses import (
    quiet_hours_runtime as runtime,
)
from custom_components.extended_openai_conversation_responses.quiet_hours import (
    QuietHoursManager,
)
from homeassistant.exceptions import HomeAssistantError
from tests.quiet_hours_transition_histories import (
    HISTORIES,
    VOLUME_RETARGET_HISTORIES,
    exercise_history,
    restore_while_suspended,
    retire_without_restoration,
)
from tests_real_ha.test_quiet_hours_scheduling import (
    _install_control_services,
    _install_satellite_entities,
)


@pytest.fixture
def real_store_io():
    """Select native Store files through the acceptance suite's I/O fixture."""
    return True


@pytest.mark.parametrize("kind", ["volume", "switch"])
@pytest.mark.parametrize("restart", [False, True])
@pytest.mark.parametrize("returned_quiet", [False, True])
async def test_offline_control_rejoins_later_period_with_durable_baseline(
    hass, monkeypatch, real_store_io, kind, restart, returned_quiet
):
    await hass.config.async_set_time_zone("UTC")
    now = datetime(2026, 11, 1, 22, 30, tzinfo=UTC)
    monkeypatch.setattr(runtime.dt_util, "now", lambda: now)
    _, media, wake = _install_satellite_entities(hass, volume=0.8)
    calls = []
    _install_control_services(hass, calls)
    manager = QuietHoursManager(hass)
    manager._initialized = True
    manager._config = runtime.QuietHoursConfig(enabled=True)
    store_path = Path(hass.config.path(".storage", runtime._STORAGE_KEY))
    entity = media if kind == "volume" else wake
    try:
        await manager.async_reconcile(now=now)
        original = 0.8 if kind == "volume" else True
        assert manager.active["controls"][entity]["original_value"] == original
        hass.states.async_set(entity, "unavailable")
        for day in (1, 2):
            now = datetime(2026, 11, 1 + day, 8, 30, tzinfo=UTC)
            await manager.async_reconcile(now=now)
            now = now.replace(hour=22)
            await manager.async_reconcile(now=now)
            disk = json.loads(store_path.read_text(encoding="utf-8"))["data"]
            assert disk["active"]["controls"][entity]["original_value"] == original
        if restart:
            await manager.async_shutdown()
            manager = QuietHoursManager(hass)
            await manager.async_setup()
        calls.clear()
        now = now.replace(hour=23, minute=30)
        hass.states.async_set(
            entity,
            "idle" if kind == "volume" else "off" if returned_quiet else "on",
            {"volume_level": 0.2 if returned_quiet else 0.8}
            if kind == "volume"
            else {},
        )
        for _ in range(3):
            await manager.async_reconcile(now=now)
        if kind == "volume":
            assert hass.states.get(entity).attributes["volume_level"] == 0.2
        else:
            assert hass.states.get(entity).state == "off"
        assert calls == (
            []
            if returned_quiet
            else [("volume_set" if kind == "volume" else "turn_off", entity)]
        )
        disk = json.loads(store_path.read_text(encoding="utf-8"))["data"]
        assert disk["active"]["controls"][entity]["original_value"] == original
        assert not disk["active"]["controls"][entity].get("restore_before_apply")
        now = (now + timedelta(days=1)).replace(hour=8)
        await manager.async_reconcile(now=now)
        if kind == "volume":
            assert hass.states.get(entity).attributes["volume_level"] == 0.8
        else:
            assert hass.states.get(entity).state == "on"
        assert manager.active is None
    finally:
        await manager.async_shutdown()


@pytest.mark.parametrize("deferred", [False, True])
@pytest.mark.parametrize("restart", [False, True])
async def test_available_quiet_effect_carries_across_missed_daytime_boundary(
    hass, monkeypatch, real_store_io, deferred, restart
):
    """No daytime-volume command is valid while the same ceiling is required."""
    await hass.config.async_set_time_zone("UTC")
    now = datetime(2026, 11, 1, 22, 30, tzinfo=UTC)
    monkeypatch.setattr(runtime.dt_util, "now", lambda: now)
    _, media, _ = _install_satellite_entities(hass, volume=0.8)
    calls, pending = [], []

    def report(value, context):
        hass.states.async_set(media, "idle", {"volume_level": value}, context=context)

    async def volume_set(call):
        value = call.data["volume_level"]
        calls.append(value)
        if deferred:
            pending.append((value, call.context))
        else:
            report(value, call.context)

    def feedback():
        for value, context in pending:
            report(value, context)
        pending.clear()

    hass.services.async_register("media_player", "volume_set", volume_set)
    manager = QuietHoursManager(hass)
    manager._initialized = True
    manager._config = runtime.QuietHoursConfig(enabled=True, wake_sound="unchanged")
    try:
        await manager.async_reconcile(now=now)
        feedback()
        assert calls == [0.2]
        calls.clear()
        now += timedelta(days=1)
        if restart:
            # Crash-style replacement: no shutdown restoration or daytime read.
            manager = QuietHoursManager(hass)
            await manager.async_setup()
        for _ in range(4):
            await manager.async_reconcile(now=now)
            feedback()
            assert calls == []
            assert hass.states.get(media).attributes["volume_level"] == 0.2
            assert manager.active["controls"][media]["original_value"] == 0.8
            now += timedelta(minutes=5)
        disk = json.loads(
            Path(hass.config.path(".storage", runtime._STORAGE_KEY)).read_text(
                encoding="utf-8"
            )
        )["data"]
        assert disk["active"]["controls"][media]["original_value"] == 0.8
        now = (now + timedelta(days=1)).replace(hour=8)
        await manager.async_reconcile(now=now)
        feedback()
        assert calls == [0.8]
        assert hass.states.get(media).attributes["volume_level"] == 0.8
        assert manager.active is None
    finally:
        await manager.async_shutdown()


class NativeTransitionDriver:
    """Actual HA registry/state/services and Store files; no decision mocks."""

    def __init__(self, hass, monkeypatch, kind, deferred):
        self.hass, self.kind, self.deferred = hass, kind, deferred
        self.values = (
            {"quiet": 0.2, "baseline": 0.8, "alternate": 0.6, "retargeted": 0.3}
            if kind == "volume"
            else {"quiet": False, "baseline": True, "alternate": True}
        )
        _, media, wake = _install_satellite_entities(
            hass, volume=0.8 if kind == "volume" else 0.1
        )
        self.entity = media if kind == "volume" else wake
        self.commands, self.pending = [], []
        self.command_contexts = []
        self.lose_ack = self.reject = False
        self.contextless = self.hold_feedback = False
        self.now = datetime(2026, 11, 1, 22, 30, tzinfo=UTC)
        monkeypatch.setattr(runtime.dt_util, "now", lambda: self.now)
        self.manager = QuietHoursManager(hass)
        self.manager._initialized = True
        self.manager._config = runtime.QuietHoursConfig(
            enabled=True, wake_sound="unchanged" if kind == "volume" else "off"
        )
        self.departed = False

        async def service(call):
            value = (
                call.data.get("volume_level")
                if call.domain == "media_player"
                else call.service == "turn_on"
            )
            self.commands.append(
                (call.domain, call.service, call.data["entity_id"], value)
            )
            self.command_contexts.append(call.context)
            assert call.data["entity_id"] == self.entity
            assert call.domain == ("media_player" if kind == "volume" else "switch")
            if self.reject:
                self.reject = False
                raise HomeAssistantError("Service rejected without a report")
            context = None if self.contextless else call.context
            self.contextless = False
            if self.lose_ack:
                self.lose_ack = False
                self.report(value, context)
                raise HomeAssistantError("Acknowledgement lost after device report")
            if deferred or self.hold_feedback:
                self.pending.append((value, context))
            else:
                self.report(value, context)

        hass.services.async_register("media_player", "volume_set", service)
        hass.services.async_register("switch", "turn_on", service)
        hass.services.async_register("switch", "turn_off", service)

    def report(self, value, context=None):
        if self.kind == "volume":
            self.hass.states.async_set(
                self.entity, "idle", {"volume_level": value}, context=context,
                force_update=True,
            )
        else:
            self.hass.states.async_set(
                self.entity, "on" if value else "off", context=context,
                force_update=True,
            )

    def offline(self):
        self.hass.states.async_set(self.entity, "unavailable")

    def current(self):
        state = self.hass.states.get(self.entity)
        if state.state == "unavailable":
            return None
        return (
            state.attributes["volume_level"]
            if self.kind == "volume"
            else state.state == "on"
        )

    def feedback(self):
        if self.hold_feedback:
            return
        for value, context in self.pending:
            self.report(value, context)
        self.pending.clear()

    def policy_active(self):
        return self.hass.states.get(self.manager._state_entity_id()).state == "on"

    def depart(self):
        # Discovery is an input to reconciliation: this control is no longer selected.
        self.departed = True
        self.manager._discover_capabilities = lambda: []

    def return_to_policy(self):
        self.departed = False
        del self.manager._discover_capabilities

    def replace_manager(self):
        retire_without_restoration(self.manager)
        self.manager = QuietHoursManager(self.hass)
        if self.departed:
            self.manager._discover_capabilities = lambda: []

    async def startup_restore(self):
        self.replace_manager()
        await restore_while_suspended(self.manager)

    async def crash(self):
        self.replace_manager()
        await self.manager.async_setup()

    async def reconcile(self, hour):
        self.now = datetime(2026, 11, 1, 22, 30, tzinfo=UTC) + timedelta(hours=hour)
        await self.manager.async_reconcile(now=self.now)


@pytest.mark.parametrize("history", HISTORIES, ids=list(HISTORIES))
@pytest.mark.parametrize("kind", ["volume", "switch"])
@pytest.mark.parametrize("deferred", [False, True])
async def test_native_transition_specification(
    hass, monkeypatch, real_store_io, history, kind, deferred
):
    await hass.config.async_set_time_zone("UTC")
    driver = NativeTransitionDriver(hass, monkeypatch, kind, deferred)
    try:
        await exercise_history(driver, HISTORIES[history])
    finally:
        await driver.manager.async_shutdown()


@pytest.mark.parametrize("history", VOLUME_RETARGET_HISTORIES)
@pytest.mark.parametrize("deferred", [False, True])
async def test_native_contextless_retarget_history(
    hass, monkeypatch, real_store_io, history, deferred
):
    await hass.config.async_set_time_zone("UTC")
    driver = NativeTransitionDriver(hass, monkeypatch, "volume", deferred)
    try:
        await exercise_history(driver, VOLUME_RETARGET_HISTORIES[history])
    finally:
        await driver.manager.async_shutdown()


@pytest.mark.parametrize("restart", [False, True])
async def test_next_quiet_period_keeps_volume_when_daytime_restore_has_no_feedback(
    hass, monkeypatch, real_store_io, restart
):
    await hass.config.async_set_time_zone("UTC")
    now = datetime(2026, 11, 1, 22, 30, tzinfo=UTC)
    monkeypatch.setattr(runtime.dt_util, "now", lambda: now)
    monkeypatch.setattr(runtime.dt_util, "utcnow", lambda: now)
    _, media, _ = _install_satellite_entities(hass, volume=0.8)
    calls, pending = [], []
    defer_restore = True

    def report(value, context):
        hass.states.async_set(media, "idle", {"volume_level": value}, context=context)

    async def volume_set(call):
        value = call.data["volume_level"]
        calls.append(value)
        if defer_restore and value == 0.8:
            pending.append((value, call.context))
        else:
            report(value, call.context)

    hass.services.async_register("media_player", "volume_set", volume_set)
    manager = QuietHoursManager(hass)
    manager._initialized = True
    manager._config = runtime.QuietHoursConfig(enabled=True, wake_sound="unchanged")
    try:
        await manager.async_reconcile(now=now)
        now += timedelta(hours=10)
        await manager.async_reconcile(now=now)
        assert calls == [0.2, 0.8]
        assert hass.states.get(media).attributes["volume_level"] == 0.2

        now += timedelta(hours=14)
        defer_restore = False
        if restart:
            retire_without_restoration(manager)
            manager = QuietHoursManager(hass)
            await manager.async_setup()
        await manager.async_reconcile(now=now)
        assert calls == [0.2, 0.8]
        assert manager.active["controls"][media]["original_value"] == 0.8
        assert hass.states.get(media).attributes["volume_level"] == 0.2

        report(*pending.pop())
        await manager.async_reconcile(now=now)
        assert calls == [0.2, 0.8, 0.2]
        now += timedelta(hours=10)
        await manager.async_reconcile(now=now)
        assert calls == [0.2, 0.8, 0.2, 0.8]
        assert manager.active is None
    finally:
        defer_restore = False
        await manager.async_shutdown()


@pytest.mark.parametrize("provider_loads", [True, False])
async def test_component_bootstrap_defers_saved_restoration_until_entry_outcome(
    hass, monkeypatch, real_store_io, provider_loads
):
    from unittest.mock import AsyncMock

    from custom_components import extended_openai_conversation_responses as integration
    from custom_components.extended_openai_conversation_responses.const import DOMAIN
    from tests.test_reauthentication import _authentication_error
    from tests_real_ha.test_acceptance_lifecycle import _make_entry

    await hass.config.async_set_time_zone("UTC")
    now = datetime(2026, 11, 1, 22, 30, tzinfo=UTC)
    monkeypatch.setattr(runtime.dt_util, "now", lambda: now)
    monkeypatch.setattr(runtime.dt_util, "utcnow", lambda: now)
    _, media, _ = _install_satellite_entities(hass, volume=0.8)
    calls = []
    defer_restore = True

    async def volume_set(call):
        value = call.data["volume_level"]
        calls.append(value)
        if value != 0.8 or not defer_restore:
            hass.states.async_set(
                media, "idle", {"volume_level": value}, context=call.context
            )

    hass.services.async_register("media_player", "volume_set", volume_set)
    previous = QuietHoursManager(hass)
    previous._initialized = True
    previous._config = runtime.QuietHoursConfig(enabled=True, wake_sound="unchanged")
    await previous.async_reconcile(now=now)
    now += timedelta(hours=10)
    await previous.async_reconcile(now=now)
    assert calls == [0.2, 0.8]
    assert hass.states.get(media).attributes["volume_level"] == 0.2
    retire_without_restoration(previous)

    now += timedelta(hours=14)
    defer_restore = False
    assert DOMAIN not in hass.config.components
    entry = _make_entry("Quiet Hours cold bootstrap", include_ai_task=False)
    entry.add_to_hass(hass)
    authenticate = integration.get_authenticated_client
    calls_at_authentication = []

    async def check_bootstrap_order(**kwargs):
        calls_at_authentication.extend(calls)
        if not provider_loads:
            raise _authentication_error()
        return await authenticate(**kwargs)

    monkeypatch.setattr(
        integration,
        "get_authenticated_client",
        AsyncMock(side_effect=check_bootstrap_order),
    )
    assert await hass.config_entries.async_setup(entry.entry_id) is provider_loads
    await hass.async_block_till_done()
    assert calls_at_authentication == [0.2, 0.8]

    if provider_loads:
        assert calls == [0.2, 0.8]
        assert hass.states.get(media).attributes["volume_level"] == 0.2
        manager = await integration.async_get_quiet_hours(hass)
        assert manager.active["controls"][media]["original_value"] == 0.8
        assert await hass.config_entries.async_unload(entry.entry_id)
    else:
        assert calls == [0.2, 0.8, 0.8]
        assert hass.states.get(media).attributes["volume_level"] == 0.8
        assert "quiet_hours_manager" not in hass.data[DOMAIN]
        assert (await previous._store.async_load())["active"] is None
