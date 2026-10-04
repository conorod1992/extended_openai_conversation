"""Save validation reuse stays tied to authoritative persisted inputs."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import yaml
from homeassistant.exceptions import HomeAssistantError

from custom_components.extended_openai_conversation_responses import (
    agent_config,
    management_function_repair as repair,
    management_function_quarantine as quarantine,
    management_ui,
)
from tests.test_management_loading_performance import (
    _hass_with_agent as base_agent,
    _persisted_invalid_function_tools,
)


def _hass_with_agent():
    hass, entry, subentry = base_agent()
    subentry.data = {
        **subentry.data,
        "functions": yaml.safe_dump(
            [
                {
                    "spec": {
                        "name": "native_probe",
                        "description": "Probe",
                        "parameters": {"type": "object", "properties": {}},
                    },
                    "function": {"type": "native", "name": "get_user_from_user_id"},
                    "enabled": True,
                }
            ]
        ),
        "function_groups": [
            {
                "id": "probe-group",
                "name": "Probe",
                "description": "Probe tools",
                "enabled": True,
                "loading_mode": "on_demand",
                "functions": ["native_probe"],
                "guest_allowed": False,
            }
        ],
    }
    return hass, entry, subentry


def warm(subentry):
    projection = repair.persisted_config_projection(subentry)
    snapshot, _ = repair.normalized_persisted_config_snapshot(projection)
    return snapshot, projection.revision


@pytest.mark.parametrize(
    "operation",
    ["tool", "group_description", "group_enabled", "group_members", "runtime_group"],
)
def test_function_save_validates_once_and_reuses_group_only_tools(
    monkeypatch, operation
):
    hass, entry, subentry = _hass_with_agent()
    before, revision = warm(subentry)
    raw_yaml = subentry.data["functions"]
    tools, groups = deepcopy(before["functions"]), deepcopy(before["function_groups"])
    if operation == "tool":
        tools[0]["enabled"] = False
    elif operation == "runtime_group":
        from custom_components.extended_openai_conversation_responses.functions.base import (
            _RuntimeFunctionConfig,
        )

        tools[0]["function"] = _RuntimeFunctionConfig(
            {"type": "native", "name": object()}, deepcopy(tools[0]["function"])
        )
        groups[0]["description"] = "Runtime group edit"
    elif operation == "group_description":
        groups[0]["description"] = "Updated description"
    elif operation == "group_enabled":
        groups[0]["enabled"] = not groups[0]["enabled"]
    else:
        groups[0]["functions"] = []
    normalization = Mock(wraps=agent_config.normalize_agent_config)
    validation = Mock(wraps=agent_config.validate_function_tools)
    emission = Mock(wraps=yaml.safe_dump)
    parsing = Mock(wraps=yaml.safe_load)
    monkeypatch.setattr(agent_config, "normalize_agent_config", normalization)
    monkeypatch.setattr(agent_config, "validate_function_tools", validation)
    monkeypatch.setattr(yaml, "safe_dump", emission)
    monkeypatch.setattr(yaml, "safe_load", parsing)
    saved = repair.persist_valid_function_configuration(
        hass, entry, subentry, tools, groups, expected_revision=revision
    )
    after, _ = repair.normalized_persisted_config_snapshot(
        repair.persisted_config_projection(subentry)
    )
    assert normalization.call_count == 1
    assert validation.call_count == (1 if operation == "tool" else 0)
    assert sum(
        bool(
            call.args[0]
            and isinstance(call.args[0], list)
            and "spec" in call.args[0][0]
        )
        for call in emission.call_args_list
    ) == (1 if operation == "tool" else 0)
    assert not any(
        isinstance(call.args[0], str) and "spec:" in call.args[0]
        for call in parsing.call_args_list
    )
    assert after["functions"] == saved["functions"] == deepcopy(tools)
    assert after["function_groups"] == saved["function_groups"] == groups
    if operation != "tool":
        assert subentry.data["functions"] == raw_yaml
    monkeypatch.undo()
    assert agent_config.agent_config_snapshot(dict(subentry.data)) == after
    with pytest.raises(HomeAssistantError, match="changed in another tab"):
        repair.persist_valid_function_configuration(
            hass, entry, subentry, tools, groups, expected_revision=revision
        )


@pytest.mark.parametrize(
    "section,action,updates",
    [
        ("guest_mode", "save_policy", {"guest_mode_enabled": True}),
        ("knowledge", "set_enabled", {"enabled": True}),
    ],
)
async def test_specialised_settings_reuse_and_seed_exact_projection(
    monkeypatch, section, action, updates
):
    hass, entry, subentry = _hass_with_agent()
    before, revision = warm(subentry)
    raw = deepcopy(subentry.data)
    monkeypatch.setattr(
        management_ui,
        "async_get_guest_mode",
        AsyncMock(return_value=SimpleNamespace(status=lambda: {})),
    )
    monkeypatch.setattr(
        management_ui,
        "async_get_knowledge",
        AsyncMock(return_value=SimpleNamespace(total_source_count=0)),
    )
    request = management_ui._ManagementRequest(
        hass,
        "admin",
        True,
        {
            "section": section,
            "action": action,
            "revision": revision,
            **({"config": updates} if section == "guest_mode" else updates),
        },
        entry.entry_id,
        subentry.subentry_id,
        entry,
        subentry,
    )
    validation = Mock(
        side_effect=AssertionError("unchanged tools must not be revalidated")
    )
    monkeypatch.setattr(agent_config, "validate_function_tools", validation)
    monkeypatch.setattr(
        yaml,
        "safe_load",
        Mock(side_effect=AssertionError("unchanged YAML must not be parsed")),
    )
    monkeypatch.setattr(
        yaml,
        "safe_dump",
        Mock(side_effect=AssertionError("unchanged YAML must not be emitted")),
    )
    with quarantine.management_function_tools(section):
        result = await (
            management_ui.async_guest_mode_command(request)
            if section == "guest_mode"
            else management_ui.async_knowledge_command(request)
        )
    assert subentry.data["functions"] == raw["functions"]
    assert subentry.data["function_groups"] == raw["function_groups"]
    after, _ = repair.normalized_persisted_config_snapshot(
        repair.persisted_config_projection(subentry)
    )
    assert after["functions"] == before["functions"]
    assert (
        after["knowledge_enabled" if section == "knowledge" else "guest_mode_enabled"]
        is True
    )
    assert result["revision"] == repair.persisted_config_projection(subentry).revision
    monkeypatch.undo()
    assert agent_config.agent_config_snapshot(dict(subentry.data)) == after
    subentry.data = {**subentry.data, "functions": "not: a tool collection"}
    with pytest.raises((HomeAssistantError, ValueError)):
        quarantine.merge_unchanged_function_configuration(
            subentry, {"knowledge_enabled": False}
        )


def test_unrelated_guest_merge_preserves_quarantined_fields_and_valid_subset():
    _hass, _entry, subentry = _hass_with_agent()
    subentry.data = {**subentry.data, "functions": _persisted_invalid_function_tools()}
    raw = deepcopy(subentry.data)
    with quarantine.management_function_tools("guest_mode"):
        persisted, snapshot = quarantine.merge_unchanged_function_configuration(
            subentry, {"guest_mode_enabled": True}
        )
    assert persisted["functions"] == raw["functions"]
    assert persisted["function_groups"] == raw["function_groups"]
    assert all(
        tool["spec"]["name"] != "invalid_phone_tool" for tool in snapshot["functions"]
    )
    assert persisted["guest_mode_enabled"] is True


def test_unchanged_merge_rejects_function_inputs():
    _hass, _entry, subentry = _hass_with_agent()
    with pytest.raises(HomeAssistantError, match="cannot change"):
        quarantine.merge_unchanged_function_configuration(subentry, {"functions": []})
