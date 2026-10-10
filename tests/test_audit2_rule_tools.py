"""Regression coverage for atomic tool mutations and native Request Rules."""

from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import (
    management_ui,
    request_rules,
)
from custom_components.extended_openai_conversation_responses.agent_config import (
    configured_function_tools_from_data,
)
from custom_components.extended_openai_conversation_responses.delayed_tools import (
    DelayedToolManager,
    tool_definition_fingerprint,
)
from custom_components.extended_openai_conversation_responses.function_dependency_integrity import (
    async_validate_static_function_arguments,
)
from custom_components.extended_openai_conversation_responses.function_execution import (
    async_validate_function_arguments,
)
from custom_components.extended_openai_conversation_responses.functions import (
    EditFileFunction,
    ReadFileFunction,
    WriteFileFunction,
)
from custom_components.extended_openai_conversation_responses.functions.bash import (
    BashFunction,
)
from custom_components.extended_openai_conversation_responses.functions.script import (
    ScriptFunction,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.template import Template
from tests.test_delayed_tools import _coverage_record, _live_entry
from tests.test_management_ui import _entry_pair
from tests.test_request_rules import local_rule, manager


async def test_typed_function_results_are_dynamic_but_runtime_remains_strict(hass):
    spec = {
        "name": "control",
        "parameters": {
            "type": "object",
            "properties": {"level": {"type": "integer"}, "mode": {"enum": ["safe"]}},
            "required": ["level", "mode"],
        },
    }
    await async_validate_static_function_arguments(
        hass, spec, {"level": "{reading.level}", "mode": "safe"}
    )
    with pytest.raises(HomeAssistantError):
        await async_validate_static_function_arguments(
            hass, spec, {"level": "{reading.level}", "mode": "unsafe"}
        )
    with pytest.raises(HomeAssistantError):
        await async_validate_function_arguments(
            hass, spec, {"level": "invalid", "mode": "safe"}
        )
    await async_validate_function_arguments(hass, spec, {"level": 2, "mode": "safe"})


def test_native_device_action_is_not_legacy_domain_syntax():
    action = {
        "device_id": "device",
        "domain": "light",
        "type": "turn_on",
        "entity_id": "light.public",
    }
    assert request_rules._validate_script_sequence([action]) == [action]


async def test_native_slot_expansion_does_not_require_function_results(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    rule = local_rule(phrases=["hello {name}"], match_type="sentence_pattern")
    rule["action"]["actions"] = [{"set_conversation_response": "Hello {name}"}]
    result = await request_rules.async_evaluate_rule(
        hass,
        await manager(rule),
        request_rules.RequestRuleRuntime(),
        "hello Sam",
        "native",
    )
    await hass.async_stop(force=True)
    assert result.successful and result.response == "Hello Sam"


async def test_rejected_duplicate_preserves_order_revision_and_durable_state(
    monkeypatch,
):
    rules = await manager(
        local_rule(name="First", order=0), local_rule(name="Second", order=1)
    )
    before, revision = deepcopy(rules._rules), rules.revision()

    def reject(*args, **kwargs):
        raise ValueError("compiled pattern budget")

    monkeypatch.setattr(request_rules, "_validate_total_pattern_states", reject)
    with pytest.raises(ValueError, match="budget"):
        await rules.async_duplicate(before[0]["id"])
    assert rules._rules == before and rules.revision() == revision


@pytest.mark.parametrize("path", ["configuration", "save", "tools"])
async def test_mutations_cannot_break_retained_required_arguments(
    hass, monkeypatch, path
):
    entry, subentry = _entry_pair()
    original = {
        "enabled": True,
        "spec": {
            "name": "control",
            "description": "Control",
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {"type": "native", "name": "execute_service_single"},
    }
    subentry.data["functions"] = [original]
    subentry.data["function_groups"] = []
    rule = local_rule()
    rule["action"]["actions"] = [
        {
            "action": "extended_openai_conversation_responses.call_function",
            "data": {"function": "control", "arguments": {}},
        }
    ]
    retained = await manager(rule)
    monkeypatch.setattr(
        management_ui, "async_get_request_rules", AsyncMock(return_value=retained)
    )
    monkeypatch.setattr(management_ui, "entry_and_agent", lambda *_: (entry, subentry))
    proposed = deepcopy(original)
    proposed["spec"]["parameters"] = {
        "type": "object",
        "properties": {"required_value": {"type": "integer"}},
        "required": ["required_value"],
    }
    base = {
        "entry_id": entry.entry_id,
        "subentry_id": subentry.subentry_id,
        "revision": management_ui._agent_config_revision(subentry.data, subentry.title),
    }
    message = (
        {
            **base,
            "section": "configuration",
            "action": "save" if path == "save" else "update",
            "config": {"functions": [proposed]},
        }
        if path in {"configuration", "save"}
        else {
            **base,
            "section": "tools",
            "action": "save",
            "original_name": "control",
            "tool": proposed,
        }
    )
    with pytest.raises(HomeAssistantError, match="required"):
        await management_ui.async_management_command(hass, "admin", True, message)
    hass.config_entries.async_update_subentry.assert_not_called()


async def test_scheduled_valid_tool_survives_unrelated_quarantine(hass, monkeypatch):
    tool = {
        "enabled": True,
        "spec": {
            "name": "control_light",
            "description": "Control",
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {"type": "native", "name": "execute_service_single"},
    }
    bad = deepcopy(tool)
    bad["spec"]["name"] = "invalid"
    bad["function"]["name"] = "not_a_native_function"
    entry = _live_entry()
    entry.subentries["agent"].data = {"functions": [tool, bad], "function_groups": []}
    current = configured_function_tools_from_data(
        {"functions": [tool], "function_groups": []}
    )[0]
    record = replace(
        _coverage_record(user_id=None),
        definition_fingerprint=tool_definition_fingerprint(current),
    )
    scheduled = DelayedToolManager(hass)
    scheduled._records = {record.call_id: record}
    scheduled._store = SimpleNamespace(async_save=AsyncMock())
    hass.config_entries.async_get_entry.return_value = entry
    agent = SimpleNamespace(_execute_function_tool=AsyncMock(return_value=object()))
    monkeypatch.setattr(scheduled, "_resolve_agent", lambda *_: agent)
    assert await scheduled._async_execute_due(record.call_id) is False
    agent._execute_function_tool.assert_awaited_once()


@pytest.mark.parametrize(
    "controls",
    [
        {"mode": "single"},
        {"mode": "queued"},
        {"mode": "restart"},
        {"max": 2},
        {"max_exceeded": "silent"},
    ],
)
def test_script_functions_reject_unsupported_shared_concurrency(controls):
    with pytest.raises(HomeAssistantError, match="do not support"):
        ScriptFunction().validate_schema({"type": "script", "sequence": [], **controls})
    assert ScriptFunction().validate_schema({"type": "script", "sequence": []})


async def test_file_write_and_default_bash_create_fresh_workspaces(
    hass, monkeypatch, tmp_path
):
    file = WriteFileFunction()
    file_root = tmp_path / "fresh-file"
    monkeypatch.setattr(file, "get_working_dir", lambda *_: file_root)
    result = await file.execute(
        hass,
        {
            "path": Template("first.txt", hass),
            "content": Template("hello", hass),
            "allow_dir": [Template(str(file_root), hass)],
        },
        {},
        None,
        [],
    )
    assert result["success"] and (file_root / "first.txt").read_text() == "hello"
    bash = BashFunction()
    shell_root = tmp_path / "fresh-shell"
    monkeypatch.setattr(bash, "get_working_dir", lambda *_: shell_root)
    result = await bash.execute(
        hass,
        {"allow_unsafe_shell": True, "command": Template("pwd", hass)},
        {},
        None,
        [],
    )
    assert "error" not in result and shell_root.is_dir()


@pytest.mark.parametrize("operation", ["read", "write", "edit", "bash"])
async def test_absolute_file_and_bash_paths_do_not_need_default_workspace(
    hass, monkeypatch, tmp_path, operation
):
    blocked_default = tmp_path / "unused-workspace"
    blocked_default.write_text("An existing file prevents mkdir")
    allowed = tmp_path / "custom-workspace"
    allowed.mkdir()
    note = allowed / "note.txt"
    note.write_text("Saved content")
    if operation == "bash":
        function = BashFunction()
        config = {
            "allow_unsafe_shell": True,
            "command": Template("pwd", hass),
            "cwd": Template(str(allowed), hass),
        }
    else:
        function = {
            "read": ReadFileFunction,
            "write": WriteFileFunction,
            "edit": EditFileFunction,
        }[operation]()
        config = {
            "path": Template(str(note), hass),
            "allow_dir": [Template(str(allowed), hass)],
        }
        if operation == "read":
            config["restrict_to_allow_dir"] = True
        elif operation == "write":
            config["content"] = Template("Updated content", hass)
        else:
            config.update(
                old_text=Template("Saved", hass), new_text=Template("Updated", hass)
            )
    monkeypatch.setattr(function, "get_working_dir", lambda *_: blocked_default)
    result = await function.execute(hass, config, {}, None, [])
    assert "error" not in result
    if operation == "read":
        assert result["content"] == "Saved content"
    elif operation == "bash":
        assert result["stdout"].strip() == str(allowed)
    else:
        assert note.read_text() == "Updated content"


def test_script_schema_can_revalidate_its_runtime_defaults():
    script = ScriptFunction()
    raw = {"type": "script", "sequence": []}
    runtime = script.validate_schema(raw)
    assert "mode" in runtime  # Home Assistant injects defaults at validation.
    again = script.validate_schema(runtime)
    assert deepcopy(again) == raw


@pytest.mark.parametrize(
    "controls",
    [{"mode": "single"}, {"mode": "queued"}, {"max": 2}, {"max_exceeded": "silent"}],
)
def test_composite_cannot_bypass_script_concurrency_validation(controls):
    from custom_components.extended_openai_conversation_responses.functions.composite import (
        CompositeFunction,
    )

    with pytest.raises(HomeAssistantError, match="do not support"):
        CompositeFunction().validate_schema(
            {
                "type": "composite",
                "sequence": [{"type": "script", "sequence": [], **controls}],
            }
        )
