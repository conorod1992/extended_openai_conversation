"""Conservative discovery and the boundary with persisted active ownership."""

import asyncio
from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.extended_openai_conversation_responses import (
    quiet_hours_runtime as qh,
)
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError
from tests.test_quiet_hours_runtime import _stateful_public_manager

SAT = "assist_satellite.bedroom"
MEDIA = "media_player.bedroom"
WAKE = "switch.bedroom_wake"
START = datetime(2026, 11, 11, 22, tzinfo=UTC)
MID = datetime(2026, 11, 12, 1, tzinfo=UTC)
END = datetime(2026, 11, 12, 7, tzinfo=UTC)
NEXT = datetime(2026, 11, 12, 22, tzinfo=UTC)


def _entry(entity_id, **changes):
    return SimpleNamespace(
        **{
            "entity_id": entity_id,
            "domain": entity_id.split(".")[0],
            "unique_id": entity_id,
            "device_id": "bedroom",
            "platform": "esphome",
            "disabled_by": None,
            "name": None,
            "original_name": "Wake sound",
            "entity_category": "config",
            **changes,
        }
    )


@pytest.fixture
def setup_controls(hass, monkeypatch):
    manager = _stateful_public_manager(hass)
    manager._config = qh.QuietHoursConfig(enabled=True)
    manager._publish_state = Mock()
    manager._reschedule = Mock()
    entries = {entity: _entry(entity) for entity in (SAT, MEDIA, WAKE)}
    registry = SimpleNamespace(
        async_get=entries.get,
        async_get_entity_id=lambda *identity: next(
            (
                entry.entity_id
                for entry in entries.values()
                if (entry.domain, entry.platform, entry.unique_id) == identity
            ),
            None,
        ),
    )
    device = SimpleNamespace(manufacturer="Nabu Casa", model="Home Assistant Voice PE")
    monkeypatch.setattr(qh.er, "async_get", lambda _: registry)
    monkeypatch.setattr(
        qh.er,
        "async_entries_for_device",
        lambda reg, device: [
            entry for entry in entries.values() if entry.device_id == device
        ],
    )
    monkeypatch.setattr(
        qh.dr, "async_get", lambda _: SimpleNamespace(async_get=lambda _: device)
    )
    monkeypatch.setattr(qh.dt_util, "now", lambda: MID)
    states = hass.data["_quiet_hours_test_states"]

    def report(entity, value, context=None):
        states[entity] = SimpleNamespace(
            entity_id=entity,
            state="idle" if entity.startswith("media_player.") else value,
            attributes={"volume_level": value}
            if entity.startswith("media_player.")
            else {},
            context=context or Context(),
        )

    report(SAT, "idle")
    report(MEDIA, 0.8)
    report(WAKE, "on")
    calls = []

    async def service(domain, name, data, *, blocking, context):
        calls.append((data["entity_id"], data.get("volume_level", name)))
        report(
            data["entity_id"],
            data.get("volume_level", "on" if name == "turn_on" else "off"),
            context,
        )

    hass.services.async_call = AsyncMock(side_effect=service)
    stored = {}

    async def save(value):
        stored.clear()
        stored.update(deepcopy(value))

    manager._store = SimpleNamespace(
        async_save=AsyncMock(side_effect=save),
        async_load=AsyncMock(side_effect=lambda: deepcopy(stored)),
    )
    return SimpleNamespace(
        manager=manager,
        hass=hass,
        entries=entries,
        registry=registry,
        device=device,
        states=states,
        report=report,
        calls=calls,
        stored=stored,
    )


def _discover(s):
    return s.manager._discover_capabilities()[0]


@pytest.mark.parametrize("count", [0, 1, 2])
@pytest.mark.parametrize("live", ["online", "unavailable", "missing", "no-volume"])
def test_registered_inventory_determines_uniqueness(setup_controls, count, live):
    s = setup_controls
    if count == 0:
        s.entries.pop(MEDIA)
    elif count == 2:
        s.entries["media_player.z_other"] = _entry(
            "media_player.z_other", platform="other", original_name="Speaker"
        )
        # No state at all for the second registered candidate.
    if live == "missing":
        s.states.pop(MEDIA)
    elif live == "unavailable":
        s.states[MEDIA].state = "unavailable"
    elif live == "no-volume":
        s.states[MEDIA].attributes.clear()
    result = _discover(s)
    assert result.media_player_entity_id == (MEDIA if count == 1 else None)
    assert len(result.media_player_candidates) == count


def test_state_names_platform_and_order_cannot_rank_candidates(setup_controls):
    s = setup_controls
    other = "media_player.aaa"
    s.entries[other] = _entry(other, platform="other", original_name="Speaker")
    for order in ((MEDIA, other), (other, MEDIA)):
        for volume in (None, 0.2, False, 0.9):
            s.states[MEDIA].attributes = {"volume_level": volume}
            s.entries[MEDIA].name = "Best Media Player"
            assert qh._pick_media_player([s.entries[key] for key in order]) is None
            assert _discover(s).media_player_entity_id is None
    s.entries[other].disabled_by = "user"
    assert _discover(s).media_player_entity_id == MEDIA
    s.entries[other].disabled_by = None
    s.entries[other].device_id = "another-device"
    assert _discover(s).media_player_entity_id == MEDIA


@pytest.mark.parametrize(
    "metadata",
    [
        "supported",
        "wrong-model",
        "wrong-maker",
        "generic",
        "user-name",
        "partial-name",
        "wrong-platform",
        "wrong-category",
        "multiple",
        "disabled",
    ],
)
def test_only_unique_supported_firmware_wake_control_is_automatic(
    setup_controls, metadata
):
    s = setup_controls
    if metadata == "wrong-model":
        s.device.model = "Custom satellite"
    if metadata == "wrong-maker":
        s.device.manufacturer = "Other"
    if metadata == "generic":
        s.entries[WAKE].original_name = "Mute"
    if metadata == "user-name":
        s.entries[WAKE].original_name = "Mute"
        s.entries[WAKE].name = "Wake sound"
    if metadata == "partial-name":
        s.entries[WAKE].original_name = "Wake sound amplifier"
    if metadata == "wrong-platform":
        s.entries[WAKE].platform = "other"
    if metadata == "wrong-category":
        s.entries[WAKE].entity_category = None
    if metadata == "multiple":
        s.entries["switch.another_wake"] = _entry("switch.another_wake")
    if metadata == "disabled":
        s.entries[WAKE].disabled_by = "user"
    # Localised user names and entity IDs do not change firmware role metadata.
    s.entries[WAKE].name = "Son de réveil"
    result = _discover(s)
    assert result.wake_sound_entity_id == (WAKE if metadata == "supported" else None)
    assert result.wake_sound_ambiguous is (metadata == "multiple")


def test_manual_selections_win_even_with_generic_switches_and_multiple_players(
    setup_controls,
):
    s = setup_controls
    s.entries["media_player.other"] = _entry("media_player.other")
    s.entries[WAKE].original_name = "Other"
    s.manager._config = qh._config_from_data(
        {
            "overrides": {
                SAT: {
                    "media_player_entity_id": MEDIA,
                    "wake_sound_entity_id": WAKE,
                }
            }
        }
    )
    result = _discover(s)
    assert result.media_player_entity_id == MEDIA
    assert result.wake_sound_entity_id == WAKE
    assert result.media_player_source == result.wake_sound_source == "manual"


def test_unloaded_inventory_never_falls_back_to_live_controls(
    setup_controls, monkeypatch
):
    s = setup_controls

    def unloaded(*_):
        raise AttributeError("not loaded", name="entities")

    monkeypatch.setattr(qh.er, "async_entries_for_device", unloaded)
    assert _discover(s).media_player_entity_id is None
    assert _discover(s).wake_sound_entity_id is None


def test_child_device_without_firmware_metadata_requires_manual_wake_selection(
    setup_controls,
):
    s = setup_controls
    del s.device.manufacturer
    del s.device.model
    result = _discover(s)
    assert result.media_player_entity_id == MEDIA
    assert result.wake_sound_entity_id is None
    assert not result.wake_sound_ambiguous


def _add_ambiguity(s):
    s.entries["media_player.other"] = _entry("media_player.other")
    s.entries["switch.other_wake"] = _entry("switch.other_wake")


@pytest.mark.parametrize("restart", [False, True])
async def test_ambiguity_retains_current_period_only_without_selecting_or_persisting_override(
    setup_controls, restart
):
    s = setup_controls
    await s.manager.async_reconcile(now=START)
    baseline = deepcopy(s.manager.active["controls"])
    _add_ambiguity(s)
    if restart:
        s.manager._active = s.manager._normalize_active(deepcopy(s.stored["active"]))
    await s.manager.async_reconcile(now=MID)
    assert s.calls == [(MEDIA, 0.2), (WAKE, "turn_off")]
    assert s.manager.active["controls"] == baseline
    assert _discover(s).media_player_entity_id is None
    assert _discover(s).wake_sound_entity_id is None
    assert s.manager.config.overrides == ()
    assert s.stored["config"]["overrides"] == {}
    await s.manager.async_reconcile(now=END)
    assert s.calls[-2:] == [(MEDIA, 0.8), (WAKE, "turn_on")]
    await s.manager.async_reconcile(now=NEXT)
    assert s.manager.active["controls"] == {}
    assert len(s.calls) == 4


async def test_unavailable_obligation_survives_ambiguity_period_end_and_restart(
    setup_controls,
):
    s = setup_controls
    await s.manager.async_reconcile(now=START)
    _add_ambiguity(s)
    s.states[MEDIA].state = s.states[WAKE].state = "unavailable"
    await s.manager.async_reconcile(now=MID)
    assert all(
        not c.get("restoration_pending") for c in s.manager.active["controls"].values()
    )
    await s.manager.async_reconcile(now=END)
    assert all(c["restoration_pending"] for c in s.manager.active["controls"].values())
    s.manager._active = s.manager._normalize_active(deepcopy(s.stored["active"]))
    s.report(MEDIA, 0.2)
    s.report(WAKE, "off")
    await s.manager.async_reconcile(now=END)
    assert s.calls[-2:] == [(MEDIA, 0.8), (WAKE, "turn_on")]
    assert s.manager.active is None


@pytest.mark.parametrize("kind", ["volume", "switch"])
async def test_independent_change_during_ambiguity_releases_and_is_never_reclaimed(
    setup_controls, kind
):
    s = setup_controls
    await s.manager.async_reconcile(now=START)
    _add_ambiguity(s)
    entity = MEDIA if kind == "volume" else WAKE
    s.report(entity, 0.5 if kind == "volume" else "on")
    await s.manager.async_reconcile(now=MID)
    assert entity not in s.manager.active["controls"]
    s.entries.pop("media_player.other")
    s.entries.pop("switch.other_wake")
    await s.manager.async_reconcile(now=MID)
    await s.manager.async_reconcile(now=END)
    assert len([c for c in s.calls if c[0] == entity]) == 1


async def test_old_guessed_wake_role_is_retained_only_until_period_end(setup_controls):
    s = setup_controls
    await s.manager.async_reconcile(now=START)
    s.device.model = "Custom satellite"
    await s.manager.async_reconcile(now=MID)
    assert WAKE in s.manager.active["controls"]
    assert _discover(s).wake_sound_entity_id is None
    await s.manager.async_reconcile(now=END)
    await s.manager.async_reconcile(now=NEXT)
    assert WAKE not in s.manager.active["controls"]
    assert len([c for c in s.calls if c[0] == WAKE]) == 2


async def test_new_automatic_wake_candidate_cannot_take_over_existing_ownership(
    setup_controls,
):
    s = setup_controls
    await s.manager.async_reconcile(now=START)
    s.entries[WAKE].original_name = "Other wake sound"
    other = "switch.recognised_wake"
    s.entries[other] = _entry(other)
    s.report(other, "on")
    assert _discover(s).wake_sound_entity_id == other
    await s.manager.async_reconcile(now=MID)
    assert s.calls == [(MEDIA, 0.2), (WAKE, "turn_off")]
    assert WAKE in s.manager.active["controls"]
    assert other not in s.manager.active["controls"]
    await s.manager.async_reconcile(now=END)
    await s.manager.async_reconcile(now=NEXT)
    assert (other, "turn_off") in s.calls


async def test_earlier_development_record_without_satellite_binding_keeps_verified_owner(
    setup_controls,
):
    s = setup_controls
    await s.manager.async_reconcile(now=START)
    for control in s.manager._active["controls"].values():
        control.pop("satellite_registry_identity")
    _add_ambiguity(s)
    await s.manager.async_reconcile(now=MID)
    assert s.calls == [(MEDIA, 0.2), (WAKE, "turn_off")]
    await s.manager.async_reconcile(now=END)
    assert s.calls[-2:] == [(MEDIA, 0.8), (WAKE, "turn_on")]


@pytest.mark.parametrize("lost_ack", [False, True])
async def test_ambiguous_prepared_effect_is_not_replayed_and_restores_only_our_effect(
    setup_controls, lost_ack
):
    s = setup_controls
    original_service = s.hass.services.async_call.side_effect

    async def fail(domain, name, data, **kwargs):
        if lost_ack:
            await original_service(domain, name, data, **kwargs)
        raise HomeAssistantError("lost acknowledgement")

    s.hass.services.async_call.side_effect = fail
    await s.manager.async_reconcile(now=START)
    _add_ambiguity(s)
    s.hass.services.async_call.side_effect = original_service
    await s.manager.async_reconcile(now=MID)
    assert len(s.calls) == (2 if lost_ack else 0)
    await s.manager.async_reconcile(now=END)
    assert len(s.calls) == (4 if lost_ack else 0)


@pytest.mark.parametrize(
    "change", ["rename", "remove", "reuse", "move-device", "replace-satellite"]
)
async def test_ambiguous_ownership_checks_registry_identity(setup_controls, change):
    s = setup_controls
    await s.manager.async_reconcile(now=START)
    _add_ambiguity(s)
    if change == "rename":
        entry = s.entries.pop(MEDIA)
        entry.entity_id = "media_player.renamed"
        s.entries[entry.entity_id] = entry
        s.states[entry.entity_id] = s.states.pop(MEDIA)
        s.states[entry.entity_id].entity_id = entry.entity_id
    elif change == "remove":
        s.entries.pop(MEDIA)
    elif change == "reuse":
        s.entries[MEDIA] = _entry(MEDIA, unique_id="replacement")
    elif change == "move-device":
        s.entries[MEDIA].device_id = "another-device"
    else:
        s.entries[SAT] = _entry(SAT, unique_id="replacement-satellite")
    await s.manager.async_reconcile(now=MID)
    if change == "rename":
        assert "media_player.renamed" in s.manager.active["controls"]
        assert len(s.calls) == 2
    elif change in {"remove", "reuse"}:
        assert MEDIA not in s.manager.active["controls"]
        assert (MEDIA, 0.8) not in s.calls
    else:
        # These are real association departures, not mere discovery ambiguity.
        assert MEDIA not in s.manager.active["controls"]
        assert (MEDIA, 0.8) in s.calls


@pytest.mark.parametrize("clear", [False, True])
@pytest.mark.parametrize("kind", ["volume", "switch"])
async def test_explicit_midperiod_selection_uses_departure_contract(
    setup_controls, clear, kind
):
    s = setup_controls
    entity, field, other, baseline, quiet = (
        (MEDIA, "media_player_entity_id", "media_player.other", 0.8, 0.2)
        if kind == "volume"
        else (WAKE, "wake_sound_entity_id", "switch.other_wake", "turn_on", "turn_off")
    )
    config = s.manager.config.as_dict()
    config["overrides"] = {SAT: {field: entity}}
    await s.manager.async_update_config(config)
    _add_ambiguity(s)
    s.report(other, 0.7 if kind == "volume" else "on")
    config["overrides"] = {} if clear else {SAT: {field: other}}
    await s.manager.async_update_config(config)
    assert (entity, baseline) in s.calls
    assert ((other, quiet) in s.calls) is not clear
    assert entity not in s.manager.active["controls"]


@pytest.mark.parametrize("after_commit", [False, True])
@pytest.mark.parametrize("failure", [OSError, asyncio.CancelledError])
async def test_failed_manual_clear_save_restores_authoritative_ownership(
    setup_controls,
    after_commit,
    failure,
):
    s = setup_controls
    config = s.manager.config.as_dict()
    config["overrides"] = {SAT: {"media_player_entity_id": MEDIA}}
    await s.manager.async_update_config(config)
    _add_ambiguity(s)
    save = s.manager._store.async_save.side_effect
    failed = False

    async def fail_once(value):
        nonlocal failed
        if failed:
            return await save(value)
        failed = True
        assert value["config"]["overrides"] == {}
        assert value["active"]["controls"][MEDIA]["restoration_pending"]
        if after_commit:
            await save(value)
        raise failure("interrupted write")

    s.manager._store.async_save.side_effect = fail_once
    config["overrides"] = {}
    with pytest.raises(failure):
        await s.manager.async_update_config(config)
    if after_commit:
        assert s.manager.config.overrides == ()
        assert MEDIA not in s.manager.active["controls"]
        assert (MEDIA, 0.8) in s.calls
    else:
        assert s.manager.config.overrides[0].media_player_entity_id == MEDIA
        assert not s.manager.active["controls"][MEDIA].get("restoration_pending")
        assert (MEDIA, 0.8) not in s.calls


@pytest.mark.parametrize("observed_end", [False, True])
async def test_ambiguous_unavailable_owner_does_not_carry_into_next_occurrence(
    setup_controls, observed_end
):
    s = setup_controls
    await s.manager.async_reconcile(now=START)
    _add_ambiguity(s)
    s.states[MEDIA].state = s.states[WAKE].state = "unavailable"
    if observed_end:
        await s.manager.async_reconcile(now=END)
    await s.manager.async_reconcile(now=NEXT)
    assert all(c["restoration_pending"] for c in s.manager.active["controls"].values())
    s.manager._active = s.manager._normalize_active(deepcopy(s.stored["active"]))
    s.report(MEDIA, 0.2)
    s.report(WAKE, "off")
    await s.manager.async_reconcile(now=NEXT)
    assert s.calls[-2:] == [(MEDIA, 0.8), (WAKE, "turn_on")]
    assert s.manager.active["controls"] == {}
