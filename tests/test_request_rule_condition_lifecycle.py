"""Native condition semantics and listener ownership across rule lifetimes."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from homeassistant.components.light import condition as light_conditions
from homeassistant.components.light.condition import BrightnessCondition
from homeassistant.core import State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import condition

from custom_components.extended_openai_conversation_responses import (
    request_rules as rr,
)
from custom_components import extended_openai_conversation_responses as integration
from custom_components.extended_openai_conversation_responses.request_rule_match_preview import (
    async_request_rule_match_preview,
)
from tests.test_request_rules import MemoryStore, local_rule


async def saved_rule(config):
    raw = local_rule()
    raw["conditions"] = config
    rules = rr.RequestRules(MemoryStore())
    await rules.async_initialize()
    rule = await rules.async_create(raw)
    return rules, rule


@pytest.mark.parametrize("allowed", [True, False])
async def test_legacy_callable_receives_hass_and_capture_variables(
    hass, monkeypatch, allowed
):
    seen = []

    async def factory(_hass, config):
        def check(actual_hass, variables):
            seen.append((actual_hass, variables))
            return allowed

        return check

    monkeypatch.setattr(condition, "async_from_config", factory)
    rules, _ = await saved_rule(
        [{"condition": "template", "value_template": "{{ true }}"}]
    )
    preview = await async_request_rule_match_preview(hass, rules, "good night")
    assert preview["matched"] is allowed
    assert (await rules.async_match(hass, "good night") is not None) is allowed
    assert seen == [(hass, {"request": {"slots": {}}})] * 2


@pytest.mark.parametrize("other", [None, True, False])
async def test_native_disabled_condition_agrees_in_live_and_preview(hass, other):
    configs = [
        {"condition": "template", "value_template": "{{ false }}", "enabled": False}
    ]
    if other is not None:
        configs.append(
            {
                "condition": "template",
                "value_template": "{{ true }}" if other else "{{ false }}",
            }
        )
    rules, _ = await saved_rule(configs)
    try:
        assert (await async_request_rule_match_preview(hass, rules, "good night"))[
            "matched"
        ] is (other is not False)
        assert (await rules.async_match(hass, "good night") is not None) is (
            other is not False
        )
    finally:
        rules.async_unload()


@pytest.mark.parametrize("failure", [RuntimeError, asyncio.CancelledError])
async def test_partial_checker_setup_failure_releases_preceding_checkers(
    hass, monkeypatch, failure
):
    unload = Mock()
    calls = 0

    async def factory(_hass, config):
        nonlocal calls
        calls += 1
        if calls % 2 == 0:
            raise failure()
        return SimpleNamespace(async_check=lambda **_: True, async_unload=unload)

    monkeypatch.setattr(condition, "async_from_config", factory)
    rules, _ = await saved_rule(
        [{"condition": "template", "value_template": "{{ true }}"}] * 2
    )
    expected = HomeAssistantError if failure is RuntimeError else failure
    for attempt in range(2):
        with pytest.raises(expected):
            await rules.async_match(hass, "good night")
        assert unload.call_count == attempt + 1
        assert not rules._condition_checkers


@pytest.fixture
def native_duration(hass, monkeypatch):
    listeners = []

    async def track(_hass, target, listener, entity_filter, on_update, **kwargs):
        record = (listener, entity_filter, on_update)
        listeners.append(record)
        return lambda: listeners.remove(record)

    async def platform(_hass, key):
        return "light", light_conditions

    monkeypatch.setattr(
        condition, "async_track_target_selector_state_change_event", track
    )
    monkeypatch.setattr(condition, "_async_get_condition_platform", platform)
    monkeypatch.setattr(
        condition,
        "async_extract_referenced_entity_ids",
        lambda *_args, **_kwargs: SimpleNamespace(
            referenced={"light.kitchen"}, indirectly_referenced=set()
        ),
    )
    hass.states.get.return_value = State("light.kitchen", "on", {"brightness": 255})
    return listeners


@pytest.mark.parametrize(
    "operation", ["update", "delete", "restore", "rollback", "invalidate", "unload"]
)
async def test_native_duration_listener_is_released_on_retirement(
    hass, native_duration, operation
):
    rules, rule = await saved_rule(
        [
            {
                "condition": "light.is_brightness",
                "target": {"entity_id": ["light.kitchen"]},
                "options": {
                    "for": {"minutes": 1},
                    "threshold": {"type": "above", "value": {"number": 50}},
                },
            }
        ]
    )
    # Only the external tracker is instrumented; native validation/setup is real.
    assert await rules.async_match(hass, "good night") is None
    checker = rules._condition_checkers[rule["id"]]._checkers[0]
    assert isinstance(checker, BrightnessCondition)
    assert len(native_duration) == 1
    if operation == "update":
        edited = deepcopy(rule)
        edited["conditions"] = []
        await rules.async_update(rule["id"], edited)
    elif operation == "delete":
        await rules.async_delete(rule["id"])
    elif operation == "restore":
        await rules.async_replace_backup(await rules.async_backup_data())
    elif operation == "rollback":
        rules._restore_committed_state()
    elif operation == "invalidate":
        rules._invalidate_after_unreadable_store()
    else:
        unsubscribe = rules._unsubscribe_stop = Mock()
        rules.async_unload()
        unsubscribe.assert_called_once()
    assert not native_duration
    assert checker._unloaded
    rules.async_unload()  # Disposal is idempotent.


async def test_entry_unload_disposes_manager_before_eviction(hass, monkeypatch):
    rules, _ = await saved_rule(
        [{"condition": "template", "value_template": "{{ true }}"}]
    )
    await rules.async_match(hass, "good night")
    conditions = next(iter(rules._condition_checkers.values()))
    checker = conditions._checkers[0]
    key = ("entry", "agent")
    hass.data[integration._REQUEST_RULE_MANAGERS] = {key: rules}
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
    monkeypatch.setattr(integration, "async_unload_templates", AsyncMock())
    entry = SimpleNamespace(
        entry_id="entry", subentries={"agent": SimpleNamespace(subentry_id="agent")}
    )
    assert await integration.async_unload_entry(hass, entry)
    assert key not in hass.data[integration._REQUEST_RULE_MANAGERS]
    assert not conditions._checkers
    assert checker._unloaded


async def test_manager_stop_listener_disposes_cached_checkers(hass, monkeypatch):
    store = MemoryStore()
    monkeypatch.setattr(
        rr,
        "RequestRuleStore",
        lambda *_args, **_kwargs: SimpleNamespace(bind_agent=lambda *_: store),
    )
    hass.config_entries.async_get_entry.return_value = None
    rules = await rr.async_get_request_rules(hass, "entry", "agent")
    raw = local_rule()
    raw["conditions"] = [{"condition": "template", "value_template": "{{ true }}"}]
    await rules.async_create(raw)
    await rules.async_match(hass, "good night")
    checker = next(iter(rules._condition_checkers.values()))._checkers[0]
    on_stop = hass.bus.async_listen_once.call_args.args[1]
    on_stop(None)
    hass.bus.async_listen_once.return_value.assert_not_called()
    assert checker._unloaded
    assert not rules._condition_checkers
    assert rules._unsubscribe_stop is None
