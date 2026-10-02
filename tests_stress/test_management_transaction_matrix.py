"""Nightly fault boundaries for linked Function and Request Rule edits."""

from __future__ import annotations

import asyncio
from copy import deepcopy

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.extended_openai_conversation_responses.agent_config import (
    configured_function_tools_from_data,
    validate_function_groups,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_FUNCTION_GROUPS,
    CONF_FUNCTION_TOOLS,
    CONF_SKIP_AUTHENTICATION,
    CONFIG_ENTRY_VERSION,
    DOMAIN,
)
from custom_components.extended_openai_conversation_responses.management_function_repair import (
    persisted_config_projection,
    require_agent_config_revision,
)
from custom_components.extended_openai_conversation_responses.management_ui import (
    _persist_function_configuration,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    async_get_request_rules,
)
from homeassistant.const import CONF_API_KEY
from homeassistant.exceptions import HomeAssistantError
from tests_stress.conftest import record


async def _entry(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Management transaction",
        data={CONF_API_KEY: "sk-local", CONF_SKIP_AUTHENTICATION: True},
        version=CONFIG_ENTRY_VERSION,
        subentries_data=[
            {
                "data": {},
                "subentry_type": "conversation",
                "title": "Agent",
                "unique_id": None,
            }
        ],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry, next(iter(entry.subentries.values()))


def _group(group_id: str, names: list[str]) -> dict:
    return {
        "id": group_id,
        "name": group_id,
        "description": "Nightly linked edit",
        "loading_mode": "always",
        "functions": names,
        "enabled": True,
    }


def _tool(name: str) -> dict:
    return {
        "spec": {
            "name": name,
            "description": "Nightly transaction tool",
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {"type": "template", "value_template": "transaction marker"},
        "enabled": True,
    }


@pytest.mark.parametrize(
    ("field", "intermediate"),
    [
        (CONF_FUNCTION_TOOLS, [_tool("nightly_aba")]),
        (CONF_FUNCTION_GROUPS, [_group("nightly_aba", ["nightly_aba"])]),
        ("guest_mode_enabled", False),
        ("voice_device_mappings", {"kitchen": "user:one"}),
        ("exposed_entities_enabled", False),
    ],
)
async def test_agent_config_aba_rejects_suspended_management_writer(
    hass, stress_trace, field, intermediate
) -> None:
    """A restored value cannot validate a token from before two HA updates."""
    entry, subentry = await _entry(hass)
    original = dict(subentry.data)
    if field == CONF_FUNCTION_GROUPS:
        original[CONF_FUNCTION_TOOLS] = [_tool("nightly_aba")]
        hass.config_entries.async_update_subentry(entry, subentry, data=original)
    assert original.get(field) != intermediate
    expected = persisted_config_projection(subentry).revision
    entered, resume = asyncio.Event(), asyncio.Event()

    async def stale_writer() -> None:
        entered.set()
        await resume.wait()
        require_agent_config_revision(subentry, expected)

    task = asyncio.create_task(stale_writer())
    await entered.wait()
    hass.config_entries.async_update_subentry(
        entry, subentry, data={**original, field: intermediate}
    )
    hass.config_entries.async_update_subentry(entry, subentry, data=original)
    await hass.async_block_till_done()
    assert dict(subentry.data) == original
    resume.set()
    with pytest.raises(HomeAssistantError, match="changed in another tab"):
        await task
    record(stress_trace, "agent_config_aba", field=field, revisions=3)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_function_tools_and_groups_commit_as_one_subentry_revision(
    hass,
    monkeypatch,
    stress_trace,
) -> None:
    entry, subentry = await _entry(hass)
    original = deepcopy(dict(subentry.data))
    tools = [_tool("nightly_one"), _tool("nightly_two")]
    groups = [_group("nightly_group", ["nightly_one", "nightly_two"])]
    update = hass.config_entries.async_update_subentry

    def fail_before_write(*args, **kwargs):
        raise RuntimeError("injected subentry write failure")

    monkeypatch.setattr(hass.config_entries, "async_update_subentry", fail_before_write)
    with pytest.raises(RuntimeError, match="injected subentry"):
        _persist_function_configuration(hass, entry, subentry, tools, groups)
    assert dict(subentry.data) == original
    monkeypatch.setattr(hass.config_entries, "async_update_subentry", update)
    _persist_function_configuration(hass, entry, subentry, tools, groups)
    assert {
        tool["spec"]["name"]
        for tool in configured_function_tools_from_data(subentry.data)
    } >= {"nightly_one", "nightly_two"}
    assert validate_function_groups(
        subentry.data[CONF_FUNCTION_GROUPS],
        configured_function_tools_from_data(subentry.data),
    )[0]["functions"] == ["nightly_one", "nightly_two"]
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    subentry = next(iter(entry.subentries.values()))
    assert validate_function_groups(
        subentry.data[CONF_FUNCTION_GROUPS],
        configured_function_tools_from_data(subentry.data),
    )[0]["functions"] == ["nightly_one", "nightly_two"]
    record(stress_trace, "function_group_transaction", injected_phases=1, reloads=1)


async def test_rule_group_and_references_rollback_on_store_failure(
    hass,
    monkeypatch,
    stress_trace,
) -> None:
    entry, subentry = await _entry(hass)
    rules = await async_get_request_rules(hass, entry.entry_id, subentry.subentry_id)
    await rules.async_set_groups([{"id": "old", "name": "Old"}])
    await rules.async_create(
        {
            "name": "Nightly linked rule",
            "phrases": ["nightly transaction marker"],
            "match_type": "equals",
            "action_type": "local_action",
            "action": {"actions": [{"action": "script.turn_on"}]},
            "group_id": "old",
        }
    )
    before = rules.snapshot()
    save = rules._store.async_save

    async def fail_before_write(_value):
        raise RuntimeError("injected rule store failure")

    monkeypatch.setattr(rules._store, "async_save", fail_before_write)
    with pytest.raises(RuntimeError, match="injected rule store"):
        await rules.async_set_groups([{"id": "new", "name": "New"}])
    assert rules.snapshot() == before
    monkeypatch.setattr(rules._store, "async_save", save)
    result = await rules.async_set_groups([{"id": "new", "name": "New"}])
    assert result["groups"][0]["id"] == "new"
    assert result["rules"][0]["group_id"] is None
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    reloaded = await async_get_request_rules(hass, entry.entry_id, subentry.subentry_id)
    assert reloaded.snapshot()["groups"] == result["groups"]
    assert reloaded.snapshot()["rules"][0]["group_id"] is None
    record(stress_trace, "rule_group_transaction", injected_phases=1, reloads=1)


@pytest.mark.parametrize(
    "shape",
    [
        "depth-inside",
        "width-inside",
        "depth-outside",
        "width-outside",
        "alias-cycle",
        "alias-width",
        "unsafe",
        "malformed",
    ],
)
async def test_composite_native_yaml_management_ingestion_preserves_authority(
    hass, monkeypatch, stress_trace, shape
):
    """Native YAML validation and real save reject unsafe trees before authority changes."""
    import yaml
    from custom_components.extended_openai_conversation_responses.management_ui import (
        async_management_command,
    )

    entry, subentry = await _entry(hass)

    async def command(action, **kwargs):
        current = entry.subentries[subentry.subentry_id]
        return await async_management_command(
            hass,
            "composite-admin",
            True,
            {
                "section": "tools",
                "action": action,
                "entry_id": entry.entry_id,
                "subentry_id": current.subentry_id,
                "revision": persisted_config_projection(current).revision,
                **kwargs,
            },
        )

    saved = _tool("ingestion_probe")
    await command("save", tool=saved)
    before = deepcopy(dict(entry.subentries[subentry.subentry_id].data))
    tool = _tool("ingestion_probe")
    leaf = {"type": "template", "value_template": "INGESTED-HEALTHY"}
    function = leaf
    if shape.startswith("depth"):
        for _ in range(32 if shape.endswith("inside") else 33):
            function = {"type": "composite", "sequence": [function]}
    elif shape.startswith("width") or shape == "alias-width":
        function = {
            "type": "composite",
            "sequence": [leaf] * (255 if shape == "width-inside" else 256),
        }
    else:
        function = {"type": "composite", "sequence": [leaf]}
    tool["function"] = function
    document = yaml.safe_dump(tool)
    if shape == "alias-cycle":
        document = (
            yaml.safe_dump({"spec": tool["spec"]})
            + "function: &cycle\n  type: composite\n  sequence: [*cycle]\n"
        )
    elif shape == "unsafe":
        document = "!!python/object/apply:os.system ['echo MUST-NOT-EXECUTE']"
    elif shape == "malformed":
        document = "function: [unterminated"
    if shape == "alias-width":
        assert "&id" in document and "*id" in document
    validated = await command("validate_yaml", yaml=document)
    if shape.endswith("inside"):
        assert validated["valid"], validated
        await command("save", tool=validated["config"], original_name="ingestion_probe")
        current = configured_function_tools_from_data(
            entry.subentries[subentry.subentry_id].data
        )
        assert (
            next(item for item in current if item["spec"]["name"] == "ingestion_probe")[
                "function"
            ]["type"]
            == "composite"
        )
        from custom_components.extended_openai_conversation_responses.functions import (
            get_function,
        )

        runtime = get_function("composite").validate_schema(
            validated["config"]["function"]
        )
        assert (
            await get_function("composite").execute(hass, runtime, {}, None, [])
            == "INGESTED-HEALTHY"
        )
    else:
        assert not validated["valid"] and validated["errors"], validated
        if shape in {"depth-outside", "width-outside", "alias-width"}:
            with pytest.raises((HomeAssistantError, ValueError)):
                await command("save", tool=tool, original_name="ingestion_probe")
        assert dict(entry.subentries[subentry.subentry_id].data) == before
        healthy = await command("validate_yaml", yaml=yaml.safe_dump(saved))
        assert healthy["valid"]
    record(
        stress_trace,
        "summary",
        layer="Real HA management",
        composite_ingestion_cases=1,
        composite_ingestion_rejections=int(not shape.endswith("inside")),
    )


async def test_mixed_maintenance_waiters_storage_failure_and_management_progress(
    hass, monkeypatch, stress_trace
):
    """Finite mixed contention drains despite a cancelled writer and a failed store."""
    from custom_components.extended_openai_conversation_responses.agent_maintenance import (
        get_agent_maintenance_gate,
        _async_run_exclusive_operation,
    )
    from custom_components.extended_openai_conversation_responses.management_ui import (
        async_management_command,
    )

    entry, subentry = await _entry(hass)
    gate = get_agent_maintenance_gate(hass, entry.entry_id, subentry.subentry_id)
    rules = await async_get_request_rules(hass, entry.entry_id, subentry.subentry_id)
    await rules.async_set_groups([{"id": "original", "name": "Original"}])
    before = rules.snapshot()
    reader_entered, release = asyncio.Event(), asyncio.Event()
    exclusive_entered = asyncio.Event()

    async def reader():
        async with gate.shared():
            reader_entered.set()
            await release.wait()

    async def cancelled_writer():
        async with gate.exclusive():
            raise AssertionError("Cancelled writer was admitted while reader held")

    save = rules._store.async_save

    async def storage_failure(value):
        raise RuntimeError("CONTROLLED-MAINTENANCE-STORE-FAILURE")

    async def exclusive():
        exclusive_entered.set()
        assert gate._active_readers == 0 and gate._writer_active
        monkeypatch.setattr(rules._store, "async_save", storage_failure)
        try:
            await rules.async_set_groups([{"id": "failed", "name": "Failed"}])
        finally:
            monkeypatch.setattr(rules._store, "async_save", save)

    async def command(action, **kwargs):
        return await async_management_command(
            hass,
            "maintenance-admin",
            True,
            {
                "section": "configuration" if action == "get" else "tools",
                "action": action,
                "entry_id": entry.entry_id,
                "subentry_id": subentry.subentry_id,
                "revision": persisted_config_projection(
                    entry.subentries[subentry.subentry_id]
                ).revision,
                **kwargs,
            },
        )

    held = asyncio.create_task(reader())
    await reader_entered.wait()
    restore = asyncio.create_task(_async_run_exclusive_operation(gate, exclusive))
    cancelled = asyncio.create_task(cancelled_writer())
    async with asyncio.timeout(10):
        while gate._waiting_writers != 2:
            await asyncio.sleep(0)
    readers = [asyncio.create_task(command("get")) for _ in range(3)]
    mutation = asyncio.create_task(command("save", tool=_tool("after_contention")))
    try:
        await asyncio.sleep(0)
        assert not exclusive_entered.is_set() and not any(
            task.done() for task in readers
        )
        cancelled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled
        release.set()
        await held
        with pytest.raises(RuntimeError, match="CONTROLLED-MAINTENANCE"):
            await asyncio.wait_for(restore, 10)
        await asyncio.wait_for(asyncio.gather(*readers, mutation), 15)
        assert rules.snapshot() == before
        current = configured_function_tools_from_data(
            entry.subentries[subentry.subentry_id].data
        )
        assert "after_contention" in {tool["spec"]["name"] for tool in current}
        assert gate._active_readers == gate._waiting_writers == 0
        assert not gate._writer_active and not gate._reader_depth
        await command("get")
        record(
            stress_trace,
            "summary",
            layer="Real HA management",
            mixed_maintenance_contention_cases=1,
            cancelled_maintenance_waiters=1,
            storage_failure_recoveries=1,
        )
    finally:
        release.set()
        for task in [held, restore, cancelled, *readers, mutation]:
            if not task.done():
                task.cancel()
        await asyncio.gather(
            held, restore, cancelled, *readers, mutation, return_exceptions=True
        )
