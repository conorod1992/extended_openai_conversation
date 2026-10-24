"""Cross-entry request, cold-read and quarantine service boundaries."""

import threading
from unittest.mock import AsyncMock

import pytest
import yaml

from custom_components.extended_openai_conversation_responses import (
    conversation as owner,
    management_function_repair as repair,
    management_ui,
    services,
)
from tests.test_conversation_orchestration import _assist_fixture
from tests.test_management_single_agent_prewarm import _Entry, _hass, _read_request


def _tool(name="healthy"):
    return {
        "spec": {
            "name": name,
            "description": name,
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {"type": "template", "value_template": "ok"},
    }


async def test_initial_child_guest_policy_sees_own_function_catalogue(monkeypatch):
    entity, invoke, _logs, _seen = _assist_fixture(monkeypatch)
    child, parent = _tool("child_history"), _tool("delegate")
    entity._configured_function_tools_from_data.return_value = [child]
    policy = entity._resolve_live_guest_policy.return_value
    initial = []

    def resolve():
        initial.append(
            [tool["spec"]["name"] for tool in entity._get_configured_function_tools()]
        )
        return policy

    entity._resolve_live_guest_policy.side_effect = resolve
    token = owner._ACTIVE_FUNCTION_CONFIG.set(([parent], []))
    try:
        await invoke()
        assert initial[0] == ["child_history"]
        assert owner._ACTIVE_FUNCTION_CONFIG.get() == ([parent], [])
    finally:
        owner._ACTIVE_FUNCTION_CONFIG.reset(token)


@pytest.mark.parametrize("budget", [0, 1])
async def test_zero_budget_skips_external_ha_discovery(monkeypatch, budget):
    from custom_components.extended_openai_conversation_responses.ha_llm_tools import (
        ToolSnapshot,
    )
    from tests.test_ha_llm_tools import reference

    entity, invoke, _logs, seen = _assist_fixture(monkeypatch)
    entity.subentry.data["max_function_calls_per_conversation"] = budget
    tool = _tool("external")
    tool["function"] = reference()
    entity._configured_function_tools_from_data.return_value = [tool]
    discovery = AsyncMock(return_value=ToolSnapshot())
    monkeypatch.setattr(owner, "async_discover", discovery)
    await invoke()
    assert seen
    assert discovery.await_count == int(budget > 0)


async def test_cold_multi_agent_get_decodes_saved_yaml_once_in_worker(
    hass, monkeypatch
):
    entry = _Entry("provider", 2)
    entry.mark_loaded()
    local_hass = _hass(entry)
    subentry = entry.subentries["agent-0"]
    tool = _tool("cold_unique")
    tool["function"] = {"type": "native", "name": "get_user_from_user_id"}
    subentry.data = {**subentry.data, "functions": yaml.safe_dump([tool])}
    management_ui._configuration_defaults()
    repair.discard_persisted_config_projection(subentry)
    raw = subentry.data["functions"]
    main_thread = threading.get_ident()
    calls = []
    original = yaml.safe_load

    def decode(value):
        if value == raw:
            calls.append(threading.get_ident())
        return original(value)

    monkeypatch.setattr(yaml, "safe_load", decode)
    result = await management_ui.async_configuration_command(
        _read_request(local_hass, entry)
    )
    assert result["config"]["functions"][0]["spec"]["name"] == "cold_unique"
    assert len(calls) == 1
    assert calls[0] != main_thread
    await management_ui.async_configuration_command(_read_request(local_hass, entry))
    assert len(calls) == 1


@pytest.mark.parametrize("kind", ["tools", "groups"])
@pytest.mark.parametrize("enabled", [False, True])
async def test_public_toggles_preserve_quarantined_sibling(
    hass, monkeypatch, kind, enabled
):
    from tests.test_save_validation_reuse import _hass_with_agent

    fake_hass, entry, subentry = _hass_with_agent()
    healthy = _tool()
    healthy["enabled"] = not enabled
    broken = _tool("legacy_script")
    broken["function"] = {
        "type": "script",
        "mode": "single",
        "sequence": [{"delay": 1}],
    }
    group = {
        "id": "healthy-group",
        "name": "Healthy",
        "description": "Healthy tools",
        "loading_mode": "always",
        "functions": ["healthy"],
        "enabled": not enabled,
    }
    subentry.data = {
        **subentry.data,
        "functions": yaml.safe_dump([healthy, broken]),
        "function_groups": [group],
    }
    monkeypatch.setattr(
        services,
        "resolve_memory_agent",
        lambda *_args: (entry.entry_id, subentry.subentry_id),
    )
    action = (
        services.async_set_function_tools_enabled
        if kind == "tools"
        else services.async_set_function_groups_enabled
    )
    await action(
        fake_hass,
        entry.entry_id,
        subentry.subentry_id,
        ["healthy" if kind == "tools" else "healthy-group"],
        enabled,
    )
    persisted = subentry.data
    saved = yaml.safe_load(persisted["functions"])
    assert (
        next(tool for tool in saved if tool["spec"]["name"] == "legacy_script")
        == broken
    )
    if kind == "tools":
        assert (
            next(tool for tool in saved if tool["spec"]["name"] == "healthy")["enabled"]
            is enabled
        )
    else:
        assert persisted["function_groups"][0]["enabled"] is enabled


async def test_native_diagnostic_reports_legacy_parameter_schema(hass):
    from custom_components.extended_openai_conversation_responses import agent_test
    from tests.test_agent_test import _objects

    bad = _tool("old_phone")
    bad["spec"]["parameters"]["properties"] = {
        "phone": {"type": "string", "minLength": "1"}
    }
    _fake, entry, subentry, client, _usage = _objects(
        {"functions": yaml.safe_dump([bad])}
    )
    result = await agent_test.async_test_agent(hass, entry, subentry)
    assert result.status == "Failed"
    assert any(
        check.name == "Configuration" and "minLength" in check.message
        for check in result.checks
    )
    client.chat.completions.create.assert_not_awaited()
