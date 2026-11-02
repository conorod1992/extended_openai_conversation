"""Native trigger selectors and per-assistant restore quarantine boundaries."""

import asyncio

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.extended_openai_conversation_responses import (
    agent_maintenance,
    guest_mode,
    restore_recovery,
    usage,
)
from homeassistant.components import sensor as ha_sensor
from homeassistant.helpers import entity_registry as er, trigger as trigger_helper
from tests_real_ha.test_acceptance_lifecycle import _make_entry
from tests_real_ha.test_cross_feature_acceptance import _agent, _provider, _say, _speech
from tests_real_ha.test_management_backend_acceptance import _setup_entry
from tests_real_ha.test_request_rules_script_semantics import _local


@pytest.mark.parametrize("platform", ["state", "numeric_state"])
@pytest.mark.parametrize("shape", ["direct", "grouped", "options"])
@pytest.mark.parametrize("captured", [False, True])
async def test_saved_rule_native_wait_selects_only_its_entity(
    hass, monkeypatch, platform, shape, captured
):
    agent = await _agent(hass)
    _provider(monkeypatch, agent, [])
    entity = "sensor.capture_probe"
    reference = "{entity}" if captured else entity
    options = {
        "entity_id": reference,
        **({"to": "7"} if platform == "state" else {"above": 5}),
    }
    trigger = {"trigger": platform, **options}
    if shape == "grouped":
        trigger = {"triggers": [trigger]}
    elif shape == "options":
        trigger = {"trigger": platform, "options": options}
    effects = []

    async def record(call):
        effects.append(dict(call.data))

    hass.services.async_register("capture_probe", "record", record)
    actions = [
        {"wait_for_trigger": [trigger], "timeout": 5, "continue_on_timeout": False},
        {"action": "capture_probe.record", "data": {"marker": "completed"}},
    ]
    await agent._request_rules.async_create(
        _local(
            actions,
            phrase="wait for {entity}" if captured else "wait now",
            match_type="sentence_pattern" if captured else "equals",
        )
    )
    hass.states.async_set(entity, "0")
    hass.states.async_set("sensor.unrelated", "0")
    await hass.async_block_till_done()
    ready = asyncio.Event()
    original = trigger_helper.async_initialize_triggers

    async def observe(*args, **kwargs):
        result = await original(*args, **kwargs)
        if result is not None:
            ready.set()
        return result

    monkeypatch.setattr(trigger_helper, "async_initialize_triggers", observe)
    task = asyncio.create_task(
        _say(hass, agent, "wait for " + entity if captured else "wait now")
    )
    waiter = asyncio.create_task(ready.wait())
    try:
        await asyncio.wait(
            (task, waiter), timeout=3, return_when=asyncio.FIRST_COMPLETED
        )
        assert ready.is_set(), "Saved Rule failed before attaching its native trigger"
        hass.states.async_set("sensor.unrelated", "7")
        await hass.async_block_till_done()
        assert not task.done() and effects == []
        hass.states.async_set(entity, "7")
        assert _speech(await asyncio.wait_for(task, 5)) == "Done"
        assert effects == [{"marker": "completed"}]
    finally:
        for pending in (task, waiter):
            if not pending.done():
                pending.cancel()
        await asyncio.gather(task, waiter, return_exceptions=True)


SUFFIXES = ("usage", "usage_today", "usage_month", "last_response_usage", "guest_mode")


@pytest.mark.parametrize("position", [0, 1, 2])
@pytest.mark.parametrize("blocked", [False, True])
async def test_failed_restore_isolates_sensors_and_recovery_readmits_owner(
    hass, position, blocked
):
    template = _make_entry("Sensor isolation", include_ai_task=False)
    entry = MockConfigEntry(
        domain=template.domain,
        version=template.version,
        data=dict(template.data),
        subentries_data=[
            {
                "subentry_type": "conversation",
                "title": f"Assistant {i}",
                "unique_id": None,
                "data": {},
            }
            for i in range(3)
        ],
    )
    control = _make_entry("Independent sensor provider", include_ai_task=False)
    registry = er.async_get(hass)
    for owner in (control, entry):
        owner.add_to_hass(hass)
        for sub in owner.subentries.values():
            for suffix in SUFFIXES:
                registry.async_get_or_create(
                    "sensor",
                    owner.domain,
                    f"{sub.subentry_id}_{suffix}",
                    config_entry=owner,
                    config_subentry_id=sub.subentry_id,
                    disabled_by=None,
                )
        if owner is control:
            await _setup_entry(hass, owner)
    subs = list(entry.subentries.values())
    quarantined = subs[position]
    gate = agent_maintenance.get_agent_maintenance_gate(
        hass, entry.entry_id, quarantined.subentry_id
    )
    journal = restore_recovery._journal_store(
        hass, entry.entry_id, quarantined.subentry_id
    )
    if blocked:
        await journal.async_save({"invalid_restore_journal": True})
    await restore_recovery.async_recover_pending_restores(hass)
    assert gate.recovery_required is blocked
    await _setup_entry(hass, entry)

    def entities(sub):
        component = hass.data[ha_sensor.DATA_COMPONENT]
        return {
            entity.unique_id: entity
            for entity in component.entities
            if entity.unique_id
            in {f"{sub.subentry_id}_{suffix}" for suffix in SUFFIXES}
        }

    for owner in (entry, control):
        for sub in owner.subentries.values():
            if blocked and sub is quarantined:
                assert entities(sub) == {}
                continue
            assert len(entities(sub)) == 5
            manager = await usage.async_get_usage(hass, owner.entry_id, sub.subentry_id)
            async with manager.async_run():
                await manager.async_record_request(
                    successful=True, usage=usage.RequestUsage(total_tokens=17)
                )
            guest = await guest_mode.async_get_guest_mode(
                hass, owner.entry_id, sub.subentry_id
            )
            await guest.async_update_trusted(indefinite=True)
            await hass.async_block_till_done()
            for suffix in SUFFIXES:
                entity = entities(sub)[f"{sub.subentry_id}_{suffix}"]
                assert hass.states.get(entity.entity_id).state == (
                    "active_indefinitely" if suffix == "guest_mode" else "17"
                )
    if blocked:
        assert gate.recovery_required
        await journal.async_remove()
        await restore_recovery.async_recover_pending_restores(hass)
        assert not gate.recovery_required
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert all(len(entities(sub)) == 5 for sub in subs)
    for sub in subs:
        recovered = blocked and sub is quarantined
        for suffix in SUFFIXES:
            entity = entities(sub)[f"{sub.subentry_id}_{suffix}"]
            expected = (
                ("inactive" if recovered else "active_indefinitely")
                if suffix == "guest_mode"
                else ("unknown" if suffix == "last_response_usage" else "0")
                if recovered
                else "17"
            )
            assert hass.states.get(entity.entity_id).state == expected
