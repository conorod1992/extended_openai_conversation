"""Execute authored transition/effect histories against the complete manager."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import (
    quiet_hours_runtime as runtime,
)
from homeassistant.exceptions import HomeAssistantError
from tests.quiet_hours_transition_histories import (
    HISTORIES,
    exercise_history,
    retire_without_restoration,
)
from tests.test_quiet_hours_runtime import (
    _config,
    _install_registry,
    _seed_room,
    _stateful_public_manager,
)


class MemoryTransitionDriver:
    def __init__(self, hass, monkeypatch, kind, deferred):
        self.hass, self.kind, self.deferred = hass, kind, deferred
        self.values = (
            {"quiet": 0.2, "baseline": 0.8, "alternate": 0.6}
            if kind == "volume"
            else {"quiet": False, "baseline": True, "alternate": True}
        )
        self.entity = (
            "media_player.bedroom" if kind == "volume" else "switch.bedroom_wake_sound"
        )
        self.commands, self.pending = [], []
        self.lose_ack = False
        _install_registry(monkeypatch, "bedroom")
        self.manager = _stateful_public_manager(hass)
        _seed_room(hass, "bedroom", volume=0.8)
        self.manager._config = _config(
            wake_sound="unchanged" if kind == "volume" else "off"
        )
        if kind == "switch":
            # Isolate wake-sound ownership from independent volume application.
            hass.states.async_set("media_player.bedroom", "idle", {"volume_level": 0.1})
        self.now = datetime(2026, 11, 1, 22, 30, tzinfo=UTC)
        monkeypatch.setattr(runtime.dt_util, "now", lambda: self.now)
        self.config = self.manager._config
        self.departed = False
        self.saved = None
        self.bind()
        hass.services.async_call = AsyncMock(side_effect=self.service)

    def bind(self):
        self.manager._reschedule = lambda: None

        async def save(value):
            self.saved = deepcopy(value)

        self.manager._store = SimpleNamespace(
            async_save=save,
            async_load=AsyncMock(side_effect=lambda: deepcopy(self.saved)),
        )
        if self.departed:
            self.manager._discover_capabilities = lambda: []

    def report(self, value, context=None):
        if self.kind == "volume":
            self.hass.states.async_set(self.entity, "idle", {"volume_level": value})
        else:
            self.hass.states.async_set(self.entity, "on" if value else "off")
        self.hass.states.get(self.entity).context = context or SimpleNamespace(
            id="independent"
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

    async def service(self, domain, service, data, **kwargs):
        value = (
            data["volume_level"] if domain == "media_player" else service == "turn_on"
        )
        self.commands.append((domain, service, data["entity_id"], value))
        assert data["entity_id"] == self.entity
        assert domain == ("media_player" if self.kind == "volume" else "switch")
        if self.lose_ack:
            self.lose_ack = False
            self.report(value, kwargs.get("context"))
            raise HomeAssistantError("Acknowledgement lost after attributed effect")
        if self.deferred:
            self.pending.append((value, kwargs.get("context")))
        else:
            self.report(value, kwargs.get("context"))

    def feedback(self):
        for value, context in self.pending:
            self.report(value, context)
        self.pending.clear()

    def policy_active(self):
        return self.hass.states.get(self.manager._state_entity_id()).state == "on"

    def depart(self):
        self.departed = True
        self.manager._discover_capabilities = lambda: []

    async def crash(self):
        # Drop the manager without executing graceful shutdown restoration.
        retire_without_restoration(self.manager)
        self.manager = type(self.manager)(self.hass)
        self.bind()
        await self.manager.async_setup()

    async def reconcile(self, hour):
        self.now = datetime(2026, 11, 1, 22, 30, tzinfo=UTC) + timedelta(hours=hour)
        await self.manager.async_reconcile(now=self.now)


@pytest.mark.parametrize("history", HISTORIES, ids=list(HISTORIES))
@pytest.mark.parametrize("kind", ["volume", "switch"])
@pytest.mark.parametrize("deferred", [False, True])
async def test_transition_specification(hass, monkeypatch, history, kind, deferred):
    driver = MemoryTransitionDriver(hass, monkeypatch, kind, deferred)
    await exercise_history(driver, HISTORIES[history])
