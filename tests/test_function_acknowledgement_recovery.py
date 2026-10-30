"""Function acknowledgements retain authority across Rule/storage failures."""

from copy import deepcopy
from inspect import unwrap
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import yaml

from custom_components.extended_openai_conversation_responses import management_ui as ui
from custom_components.extended_openai_conversation_responses.request_rules import (
    STORAGE_VERSION,
    RequestRules,
    RequestRuleStore,
)
from tests.test_request_rules import local_rule
from tests.test_save_validation_reuse import _hass_with_agent


@pytest.fixture
async def function_agent(hass, monkeypatch):
    _, entry, subentry = _hass_with_agent()
    monkeypatch.setattr(ui, "entry_and_agent", lambda *_: (entry, subentry))

    def apply(_entry, _subentry, *, data, **_):
        subentry.data = deepcopy(data)

    hass.config_entries.async_update_subentry.side_effect = apply
    store = RequestRuleStore(hass, STORAGE_VERSION, "rename-rules")
    rules = RequestRules(store)
    await rules.async_initialize()
    rule = local_rule("Invoke probe")
    rule["action"]["actions"] = [
        {
            "action": "extended_openai_conversation_responses.call_function",
            "data": {"function": "native_probe", "arguments": {}},
        }
    ]
    await rules.async_create(rule)
    monkeypatch.setattr(ui, "async_get_request_rules", AsyncMock(return_value=rules))
    return hass, entry, subentry, rules, store


async def send(agent, action, **extra):
    hass, entry, subentry, *_ = agent
    connection = SimpleNamespace(
        user=SimpleNamespace(id="admin", is_admin=True),
        send_error=Mock(),
        send_result=Mock(),
    )
    omit_revision = extra.pop("omit_revision", False)
    message = {
        "id": 1,
        "section": "tools",
        "action": action,
        "entry_id": entry.entry_id,
        "subentry_id": subentry.subentry_id,
        "revision": ui.persisted_config_projection(subentry).revision,
        **extra,
    }
    if omit_revision:
        message.pop("revision")
    await unwrap(ui.websocket_management)(hass, connection, message)
    return connection


async def test_disable_prepares_references_before_committing(
    function_agent, monkeypatch
):
    hass, _, subentry, rules, _ = function_agent
    original = deepcopy(subentry.data)
    # A failed reference projection must not be a definite rejection *after*
    # disabling the tool. Keep the optional projection on the preparation side.
    monkeypatch.setattr(
        rules, "function_references", Mock(side_effect=ValueError("unavailable"))
    )
    reply = await send(
        function_agent, "set_enabled", name="native_probe", enabled=False
    )
    reply.send_result.assert_not_called()
    assert reply.send_error.call_args.args[1] == "invalid_request"
    assert subentry.data == original
    hass.config_entries.async_update_subentry.assert_not_called()
    monkeypatch.setattr(rules, "function_references", lambda _: [])
    next_reply = await send(
        function_agent, "set_enabled", name="native_probe", enabled=False
    )
    next_reply.send_error.assert_not_called()
    assert next_reply.send_result.call_args.args[1]["functions"][0]["enabled"] is False


@pytest.mark.parametrize("explicit_revision", [False, True])
async def test_disable_does_not_overwrite_a_change_during_reference_preparation(
    function_agent, monkeypatch, explicit_revision
):
    hass, _, subentry, rules, _ = function_agent
    original = deepcopy(subentry.data)

    async def references(*_):
        subentry.data = {**subentry.data, "prompt": "Newer user draft"}
        return rules, {"request_rules": [], "guest_mode": False}

    monkeypatch.setattr(ui, "_function_reference_state", references)
    extra = {} if explicit_revision else {"omit_revision": True}
    reply = await send(
        function_agent, "set_enabled", name="native_probe", enabled=False, **extra
    )
    assert reply.send_error.call_args.args[1] == "invalid_request"
    assert subentry.data == {**original, "prompt": "Newer user draft"}
    hass.config_entries.async_update_subentry.assert_not_called()


@pytest.mark.parametrize("fault", ["after_write", "unreadable", "concurrent"])
async def test_retained_rename_is_uncertain_through_websocket_and_next_action(
    function_agent,
    monkeypatch,
    fault,
):
    hass, _, subentry, _rules, store = function_agent
    original_write = store._write_prepared_data
    original_load = store.async_load

    def fail(*data):
        if fault != "concurrent":
            original_write(*data)
        else:
            subentry.data = {**subentry.data, "prompt": "Concurrent owner edit"}
        raise OSError("directory sync failed")

    monkeypatch.setattr(store, "_write_prepared_data", fail)
    if fault == "unreadable":
        monkeypatch.setattr(
            store, "async_load", AsyncMock(side_effect=OSError("read failed"))
        )
    tool = yaml.safe_load(subentry.data["functions"])[0]
    tool["spec"]["name"] = "renamed_probe"
    reply = await send(function_agent, "save", original_name="native_probe", tool=tool)
    assert reply.send_error.call_args is not None, reply.send_result.call_args
    assert reply.send_error.call_args.args[1] == "eoai_mutation_uncertain"
    assert (
        yaml.safe_load(subentry.data["functions"])[0]["spec"]["name"] == "renamed_probe"
    )
    assert subentry.data["function_groups"][0]["functions"] == ["renamed_probe"]
    monkeypatch.setattr(store, "_write_prepared_data", original_write)
    monkeypatch.setattr(store, "async_load", original_load)
    fresh = RequestRules(RequestRuleStore(hass, STORAGE_VERSION, store.key))
    await fresh.async_initialize()
    referenced = "native_probe" if fault == "concurrent" else "renamed_probe"
    assert fresh.function_references(referenced)
    # An authoritative recovery identifies the split state in the concurrent
    # case. Repair the retained Rule explicitly before the next Tool edit.
    if fault == "concurrent":
        assert subentry.data["prompt"] == "Concurrent owner edit"
        await fresh.async_rename_function_reference(
            "native_probe", "renamed_probe", expected_revision=fresh.revision()
        )
    monkeypatch.setattr(ui, "async_get_request_rules", AsyncMock(return_value=fresh))
    next_reply = await send(
        function_agent, "set_enabled", name="renamed_probe", enabled=False
    )
    next_reply.send_error.assert_not_called()
    assert next_reply.send_result.call_args.args[1]["functions"][0]["enabled"] is False
    final_rules = RequestRules(RequestRuleStore(hass, STORAGE_VERSION, store.key))
    await final_rules.async_initialize()
    assert final_rules.function_references("renamed_probe")
    assert Path(store.path).exists()
