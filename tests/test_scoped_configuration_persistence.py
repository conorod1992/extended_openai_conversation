"""Partial configuration writes preserve raw legacy policy and field omissions."""

from copy import deepcopy
from dataclasses import replace
import inspect
from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.extended_openai_conversation_responses import management_ui as ui
from custom_components.extended_openai_conversation_responses.const import DOMAIN
from custom_components.extended_openai_conversation_responses.guest_mode import (
    resolve_guest_policy,
)
from custom_components.extended_openai_conversation_responses.prompt import (
    render_effective_prompt,
)
from homeassistant.config_entries import ConfigEntries, ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
)
from tests.test_request_rules import manager


def legacy_config():
    return {
        "chat_model": "gpt-4.1",
        "functions": [],
        "prompt": "{{ exposed_entities | map(attribute='entity_id') | join(',') }}",
        "guest_mode_enabled": True,
        "guest_readable_entities": ["light.guest"],
        "guest_controllable_entities": ["light.guest"],
        "guest_shared_memory_read": True,
        "guest_shared_memory_write": False,
        "future_extension": {"version": 17, "opaque": [False, None, "exact"]},
    }


class Entries:
    def __init__(self, entry):
        self.entry = entry

    def async_get_entry(self, entry_id):
        return self.entry if entry_id == self.entry.entry_id else None

    def async_entries(self, domain):
        return [self.entry] if domain == DOMAIN else []

    def async_update_subentry(self, entry, subentry, *, data, title=None):
        entry.subentries[subentry.subentry_id] = replace(
            subentry,
            data=MappingProxyType(deepcopy(data)),
            title=title or subentry.title,
        )
        return True


@pytest.fixture
async def registry_hass(tmp_path):
    """Initialize native registry prerequisites and own cleanup from construction."""
    real_hass = HomeAssistant(str(tmp_path))
    try:
        real_hass.config_entries = ConfigEntries(real_hass, {})
        await real_hass.config_entries.async_initialize()
        dr.async_setup(real_hass)
        for registry in (ar, dr, er):
            await registry.async_load(real_hass, load_empty=True)
        yield real_hass
    finally:
        await real_hass.async_stop(force=True)


async def test_archive_retention_websocket_preserves_guest_prompt(
    registry_hass, monkeypatch, hass
):
    real_hass = registry_hass
    raw = legacy_config()
    subentry = ConfigSubentry(
        data=MappingProxyType(raw),
        subentry_type="conversation",
        title="Legacy",
        unique_id=None,
    )
    entry = SimpleNamespace(
        entry_id="entry",
        domain=DOMAIN,
        data={},
        subentries={subentry.subentry_id: subentry},
    )
    real_hass.config_entries = Entries(entry)
    rules = await manager()
    monkeypatch.setattr(ui, "async_get_request_rules", AsyncMock(return_value=rules))
    real_hass.states.async_set("light.guest", "on")
    real_hass.states.async_set("lock.private", "locked")
    exposed = [
        {"entity_id": "light.guest", "name": "Guest light"},
        {"entity_id": "lock.private", "name": "Private lock"},
    ]
    active = SimpleNamespace(is_active=lambda: True)

    def prompt(options):
        policy = resolve_guest_policy(
            real_hass, options, active, exposed_entities=exposed
        )
        rendered = render_effective_prompt(
            real_hass,
            options,
            exposed_entities=exposed,
            current_device_id=None,
            user_input=SimpleNamespace(text="hello"),
            skills=[],
            guest_policy=policy,
        )
        return policy, rendered.text

    before, text = prompt(raw)
    assert "light.guest" in text and "lock.private" not in text
    connection = SimpleNamespace(
        user=SimpleNamespace(id="admin", is_admin=True),
        send_result=Mock(),
        send_error=Mock(),
    )
    message = ui.websocket_management._ws_schema(
        {
            "id": 1,
            "type": ui.WS_COMMAND,
            "entry_id": entry.entry_id,
            "subentry_id": subentry.subentry_id,
            "section": "settings",
            "action": "update",
            "settings": {"archive_retention_days": 7},
            "revision": ui.persisted_config_projection(subentry).revision,
        }
    )
    await inspect.unwrap(ui.websocket_management)(real_hass, connection, message)
    connection.send_error.assert_not_called()
    connection.send_result.assert_called_once()
    saved = entry.subentries[subentry.subentry_id].data
    after, text = prompt(saved)
    assert "lock.private" not in text
    assert after.readable_entity_ids == before.readable_entity_ids
    assert (after.shared_memory_read, after.shared_memory_write) == (
        before.shared_memory_read,
        before.shared_memory_write,
    )
    assert dict(saved) == {**raw, "archive_retention_days": 7}


@pytest.mark.parametrize("version", [None, 2])
@pytest.mark.parametrize("degraded", [False, True])
@pytest.mark.parametrize(
    "operation", ["settings", "save", "update", "knowledge", "guest_policy"]
)
async def test_public_partial_writers_preserve_unowned_raw_fields(
    hass, monkeypatch, version, degraded, operation
):
    from tests.test_management_ui import _entry_pair

    entry, subentry = _entry_pair()
    raw = legacy_config()
    raw["functions"] = "# retain original YAML formatting\n[]\n"
    raw["guest_readable_entities"].append("light.guest")
    if version is not None:
        raw.update(
            guest_policy_version=version,
            guest_excluded_entities=["lock.private"],
            guest_shared_memory_policy="read_only",
        )
    if degraded:
        from tests.test_function_repair_configuration_contract import _repairable_agent

        _entry, broken = _repairable_agent()
        raw.update(
            functions=broken.data["functions"],
            function_groups=broken.data["function_groups"],
        )
    subentry.data = deepcopy(raw)
    hass.config_entries.async_get_entry.return_value = entry

    def update(_entry, _subentry, *, data, title=None):
        subentry.data = data
        if title is not None:
            subentry.title = title

    hass.config_entries.async_update_subentry.side_effect = update
    rules = await manager()
    monkeypatch.setattr(ui, "async_get_request_rules", AsyncMock(return_value=rules))
    monkeypatch.setattr(
        ui,
        "async_get_guest_mode",
        AsyncMock(return_value=SimpleNamespace(status=lambda: {})),
    )
    monkeypatch.setattr(
        ui,
        "async_get_knowledge",
        AsyncMock(return_value=SimpleNamespace(total_source_count=0)),
    )
    monkeypatch.setattr(
        ui, "decorate_configuration_result", lambda _h, _d, result, **kw: result
    )
    revision = ui.persisted_config_projection(subentry).revision
    message = {
        "entry_id": entry.entry_id,
        "subentry_id": subentry.subentry_id,
        "revision": revision,
    }
    if operation == "settings":
        owned = {"archive_retention_days": 7}
        message.update(section="settings", action="update", settings=owned)
    elif operation in {"save", "update"}:
        owned = {"max_tokens": 777}
        message.update(section="configuration", action=operation, config=owned)
    elif operation == "knowledge":
        owned = {"knowledge_enabled": True}
        message.update(section="knowledge", action="set_enabled", enabled=True)
    else:
        owned = {"guest_policy_version": 2, "guest_excluded_entities": ["lock.private"]}
        message.update(
            section="guest_mode", action="save_policy", config=deepcopy(owned)
        )
    if degraded and operation == "save":
        # The normal editor read establishes its repair-aware save projection.
        await ui.async_management_command(
            hass,
            "admin",
            True,
            {**message, "section": "configuration", "action": "get"},
        )
    result = await ui.async_management_command(hass, "admin", True, message)
    assert result.get("valid", True)
    assert dict(subentry.data) == {**raw, **owned}
    assert raw["future_extension"] == {"version": 17, "opaque": [False, None, "exact"]}


@pytest.mark.parametrize("sampling", [{}, {"temperature": 0.0, "top_p": 0.9}])
@pytest.mark.parametrize("effort", [None, "none"])
@pytest.mark.parametrize("advanced", [False, True])
async def test_ai_task_reconfigure_preserves_omitted_fields(
    hass, sampling, effort, advanced
):
    from custom_components.extended_openai_conversation_responses.config_flow import (
        ExtendedOpenAIAITaskSubentryFlowHandler as Flow,
    )
    from homeassistant.config_entries import ConfigEntryState

    raw = {
        "chat_model": "gpt-5.6",
        "api_mode": "responses",
        **sampling,
        "future_extension": {"untouched": [0, None]},
    }
    if effort is not None:
        raw["reasoning_effort"] = effort
    # gpt-5.6 allows sampling at none; retain an explicit setting when sampling exists.
    if sampling:
        raw["reasoning_effort"] = "none"
    handler = SimpleNamespace(
        options=deepcopy(raw),
        _temp_data=None,
        _is_new=False,
        _get_entry=lambda: SimpleNamespace(state=ConfigEntryState.LOADED, data={}),
        _get_reconfigure_subentry=lambda: SimpleNamespace(data=raw),
        async_show_form=lambda **kwargs: kwargs,
        async_abort=lambda **kwargs: kwargs,
        async_update_and_abort=lambda *args, **kwargs: kwargs,
        add_suggested_values_to_schema=lambda schema, values: (
            Flow.add_suggested_values_to_schema(handler, schema, values)
        ),
    )

    async def next_step(data=None):
        return await Flow.async_step_advanced(handler, data)

    handler.async_step_advanced = next_step
    basic = await Flow.async_step_init(handler)
    edits = {"max_tokens": 777}
    if advanced:
        edits["advanced_options"] = True
    submitted = basic["data_schema"](edits)
    result = await Flow.async_step_init(handler, submitted)
    expected = {**raw, **edits}
    if advanced:
        submitted = result["data_schema"]({"shorten_tool_call_id": False})
        result = await Flow.async_step_advanced(handler, submitted)
        expected["shorten_tool_call_id"] = False
    assert result["data"] == expected
    assert handler.options == raw


async def test_degraded_repair_patch_only_persists_submitted_fields(hass, monkeypatch):
    from tests.test_function_repair_configuration_contract import _repairable_agent

    entry, subentry = _repairable_agent()
    entry.domain = DOMAIN
    raw = {**legacy_config(), **subentry.data}
    subentry.data = deepcopy(raw)
    hass.config_entries.async_get_entry.return_value = entry
    hass.config_entries.async_update_subentry.side_effect = (
        lambda _e, _s, *, data, **kw: setattr(subentry, "data", data)
    )
    rules = await manager()
    monkeypatch.setattr(ui, "async_get_request_rules", AsyncMock(return_value=rules))
    monkeypatch.setattr(
        ui, "decorate_configuration_result", lambda _h, _d, result, **kw: result
    )
    result = await ui.async_management_command(
        hass,
        "admin",
        True,
        {
            "entry_id": entry.entry_id,
            "subentry_id": subentry.subentry_id,
            "section": "function_repair",
            "action": "configuration_save",
            "config": {"max_tokens": 777},
            "revision": ui.persisted_config_projection(subentry).revision,
        },
    )
    assert result["valid"]
    assert subentry.data == {**raw, "max_tokens": 777}


async def test_ai_task_advanced_retry_does_not_promote_display_defaults(hass):
    from custom_components.extended_openai_conversation_responses.config_flow import (
        ExtendedOpenAIAITaskSubentryFlowHandler as Flow,
    )

    raw = {"chat_model": "gpt-5.6", "api_mode": "responses", "opaque": {"keep": True}}
    handler = SimpleNamespace(
        options=deepcopy(raw),
        _temp_data={"max_tokens": 777, "advanced_options": True},
        _is_new=False,
        _get_entry=lambda: SimpleNamespace(data={}),
        _get_reconfigure_subentry=lambda: SimpleNamespace(data=raw),
        async_show_form=lambda **kwargs: kwargs,
        async_update_and_abort=lambda *args, **kwargs: kwargs,
        add_suggested_values_to_schema=lambda schema, values: (
            Flow.add_suggested_values_to_schema(handler, schema, values)
        ),
    )
    failed = await Flow.async_step_advanced(handler, {"service_tier": "invalid"})
    assert failed["errors"] == {"base": "invalid_request"}
    assert handler.options == raw
    submitted = failed["data_schema"]({"service_tier": "auto"})
    saved = await Flow.async_step_advanced(handler, submitted)
    assert saved["data"] == {
        **raw,
        "max_tokens": 777,
        "advanced_options": True,
        "service_tier": "auto",
    }


async def test_ai_task_explicit_model_transition_clears_dependent_sampling(hass):
    from custom_components.extended_openai_conversation_responses.config_flow import (
        ExtendedOpenAIAITaskSubentryFlowHandler as Flow,
    )
    from homeassistant.config_entries import ConfigEntryState

    raw = {
        "chat_model": "gpt-4.1",
        "api_mode": "responses",
        "temperature": 0.2,
        "top_p": 0.9,
        "opaque": {"keep": True},
    }
    handler = SimpleNamespace(
        options=deepcopy(raw),
        _temp_data=None,
        _is_new=False,
        _get_entry=lambda: SimpleNamespace(state=ConfigEntryState.LOADED, data={}),
        _get_reconfigure_subentry=lambda: SimpleNamespace(data=raw),
        async_show_form=lambda **kwargs: kwargs,
        async_update_and_abort=lambda *args, **kwargs: kwargs,
        add_suggested_values_to_schema=lambda schema, values: (
            Flow.add_suggested_values_to_schema(handler, schema, values)
        ),
    )

    async def next_step(data=None):
        return await Flow.async_step_advanced(handler, data)

    handler.async_step_advanced = next_step
    shown = await Flow.async_step_init(
        handler, {"chat_model": "gpt-5.6", "advanced_options": True}
    )
    submitted = shown["data_schema"]({"shorten_tool_call_id": False})
    saved = await Flow.async_step_advanced(handler, submitted)
    assert saved["data"] == {
        "chat_model": "gpt-5.6",
        "api_mode": "responses",
        "reasoning_effort": "low",
        "opaque": {"keep": True},
        "advanced_options": True,
        "shorten_tool_call_id": False,
    }
    assert handler.options == raw


@pytest.mark.parametrize("degraded", [False, True])
async def test_function_rename_owns_legacy_guest_reference_update(
    hass, monkeypatch, degraded
):
    from custom_components.extended_openai_conversation_responses.function_configuration_repair import (
        editable_function_tools,
    )
    from tests.test_management_ui import _entry_pair, _tool

    entry, subentry = _entry_pair()
    raw = legacy_config()
    raw.update(functions=[_tool("old")], guest_allowed_function_names=["old"])
    if degraded:
        from tests.test_function_repair_configuration_contract import _repairable_agent

        _entry, broken = _repairable_agent()
        raw["functions"].extend(editable_function_tools(broken.data))
    subentry.data = deepcopy(raw)
    hass.config_entries.async_get_entry.return_value = entry
    hass.config_entries.async_update_subentry.side_effect = (
        lambda _e, _s, *, data, **kw: setattr(subentry, "data", data)
    )
    rules = await manager()
    monkeypatch.setattr(ui, "async_get_request_rules", AsyncMock(return_value=rules))
    await ui.async_management_command(
        hass,
        "admin",
        True,
        {
            "entry_id": entry.entry_id,
            "subentry_id": subentry.subentry_id,
            "section": "tools",
            "action": "save",
            "original_name": "old",
            "tool": _tool("new"),
            "revision": ui.persisted_config_projection(subentry).revision,
        },
    )
    assert subentry.data["guest_allowed_function_names"] == ["new"]
    assert {
        k: v
        for k, v in subentry.data.items()
        if k not in {"functions", "function_groups", "guest_allowed_function_names"}
    } == {
        k: v
        for k, v in raw.items()
        if k not in {"functions", "function_groups", "guest_allowed_function_names"}
    }
