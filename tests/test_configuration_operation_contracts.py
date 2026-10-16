"""Characterize operation projections before consolidating their policy."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses import backup
from custom_components.extended_openai_conversation_responses.agent_config import (
    GUEST_V2_FIELDS,
    AgentConfigError,
    agent_config_defaults,
    agent_config_snapshot,
    merge_agent_config,
    normalize_agent_config,
    project_agent_config_updates,
)
from custom_components.extended_openai_conversation_responses.management_ui import (
    _export_agent,
    _parse_import_document,
)
from custom_components.extended_openai_conversation_responses.request import (
    build_provider_request_snapshot,
)


@pytest.mark.parametrize("explicit_sampling", [False, True])
def test_unrelated_edit_preserves_raw_presence_and_provider_request(explicit_sampling):
    raw = {"chat_model": "gpt-4.1", "functions": [], "extension_data": {"version": 7}}
    if explicit_sampling:
        raw.update(
            temperature=agent_config_defaults()["temperature"],
            top_p=agent_config_defaults()["top_p"],
        )
    original = deepcopy(raw)
    before = build_provider_request_snapshot(raw, {}, tools_required=False)
    updates = {"prompt": "Edited"}
    normalized = merge_agent_config(raw, updates)
    saved = project_agent_config_updates(raw, updates, normalized)
    assert saved == {**raw, **updates}
    assert (
        build_provider_request_snapshot(saved, {}, tools_required=False).api_kwargs
        == before.api_kwargs
    )
    assert raw == original
    saved["extension_data"]["version"] = 8
    assert raw == original


@pytest.mark.parametrize("explicit_sampling", [False, True])
def test_portable_transfer_currently_materializes_sampling_defaults(explicit_sampling):
    raw = {"chat_model": "gpt-4.1", "functions": []}
    defaults = agent_config_defaults()
    if explicit_sampling:
        raw.update(temperature=defaults["temperature"], top_p=defaults["top_p"])
    before = build_provider_request_snapshot(raw, {}, tools_required=False).api_kwargs
    exported = _export_agent(SimpleNamespace(data=raw, title="Portable"))
    restored = _parse_import_document(exported)["config"]
    after = build_provider_request_snapshot(
        restored, {}, tools_required=False
    ).api_kwargs
    for key in ("temperature", "top_p"):
        assert (key in before) is explicit_sampling
        assert exported["config"][key] == restored[key] == after[key] == defaults[key]
    assert "reasoning_effort" not in restored


@pytest.mark.parametrize("explicit_effort", [False, True])
@pytest.mark.parametrize("quarantined", [False, True])
@pytest.mark.parametrize(
    "snapshot",
    [
        backup.export_configuration_snapshot,
        backup.recoverable_configuration_snapshot,
        backup.private_configuration_snapshot,
    ],
)
def test_recovery_projections_preserve_omission_opaque_and_legacy_fields(
    snapshot, quarantined, explicit_effort
):
    raw = {
        "chat_model": "gpt-5.2",
        "functions": [],
        "extension_data": {"version": 7},
        "guest_function_policy": "on",
    }
    if explicit_effort:
        raw["reasoning_effort"] = "none"
    if quarantined:
        raw["functions"] = [
            {"spec": {"name": "broken"}, "function": {"type": "missing"}}
        ]
    original = deepcopy(raw)
    projected = snapshot(raw)
    assert ("reasoning_effort" in projected) is explicit_effort
    if explicit_effort:
        assert projected["reasoning_effort"] == "none"
    assert projected["extension_data"] == {"version": 7}
    assert projected["guest_function_policy"] == "on"
    assert (GUEST_V2_FIELDS - {"guest_function_policy"}).isdisjoint(projected)
    if quarantined:
        assert projected["functions"] == raw["functions"]
        assert "function_groups" not in projected
    assert raw == original
    projected["extension_data"]["version"] = 8
    assert raw == original


@pytest.mark.parametrize("model_field", ["chat_model", "azure_model"])
def test_model_change_resets_reasoning_but_unrelated_edit_keeps_omission(model_field):
    current = {"chat_model": "gpt-5.2", "reasoning_effort": "high", "functions": []}
    if model_field == "azure_model":
        current["azure_model"] = "gpt-5.2"
    updates = {model_field: "gpt-5.6"}
    merged = merge_agent_config(current, updates)
    saved = project_agent_config_updates(current, updates, merged)
    expected = normalize_agent_config({**current, **updates, "reasoning_effort": "low"})
    assert saved["reasoning_effort"] == expected["reasoning_effort"] == "low"
    omitted = {
        key: value for key, value in current.items() if key != "reasoning_effort"
    }
    assert "reasoning_effort" not in merge_agent_config(omitted, {"prompt": "Edited"})
    assert "reasoning_effort" not in merge_agent_config(
        omitted, {model_field: current[model_field]}
    )


def test_display_and_explicit_reset_values_do_not_imply_persistence():
    raw = {"chat_model": "gpt-5.2", "functions": []}
    before = deepcopy(raw)
    display = agent_config_snapshot(raw)
    assert display["reasoning_effort"]
    assert "temperature" in display and "top_p" in display
    assert raw == before
    # A reset submits actual values; equal-to-default values remain explicit.
    reset = {key: display[key] for key in ("temperature", "top_p", "reasoning_effort")}
    saved = project_agent_config_updates(raw, reset, merge_agent_config(raw, reset))
    assert all(key in saved and saved[key] == value for key, value in reset.items())
    assert raw == before


async def test_duplicate_keeps_its_existing_materialized_reasoning_contract(
    hass, monkeypatch
):
    from custom_components.extended_openai_conversation_responses import management_ui
    from tests.test_management_ui import _entry_pair

    entry, subentry = _entry_pair()
    subentry.data = {
        "chat_model": "gpt-5.2",
        "functions": [],
        "extension_data": {"version": 7},
    }
    original = deepcopy(subentry.data)
    monkeypatch.setattr(
        management_ui, "entry_and_agent", lambda *_args, **_kwargs: (entry, subentry)
    )
    result = await management_ui.async_management_command(
        hass,
        "admin",
        True,
        {
            "section": "configuration",
            "action": "duplicate",
            "entry_id": entry.entry_id,
            "subentry_id": subentry.subentry_id,
        },
    )
    assert result["status"] == "created"
    duplicate = hass.config_entries.async_add_subentry.call_args.args[1]
    assert (
        duplicate.data["reasoning_effort"]
        == normalize_agent_config({"chat_model": "gpt-5.2", "functions": []})[
            "reasoning_effort"
        ]
    )
    assert "temperature" in duplicate.data and "top_p" in duplicate.data
    assert "extension_data" not in duplicate.data
    assert subentry.data == original


def test_portable_schema_excludes_opaque_export_and_rejects_unknown_import():
    raw = {"chat_model": "gpt-4.1", "functions": [], "extension_data": {"version": 7}}
    exported = _export_agent(SimpleNamespace(data=raw, title="Portable"))
    assert "extension_data" not in exported["config"]
    exported["config"]["extension_data"] = {"version": 7}
    with pytest.raises(AgentConfigError, match="unknown fields"):
        _parse_import_document(exported)


async def test_degraded_configuration_save_preserves_raw_fields_and_omissions(
    monkeypatch,
):
    from custom_components.extended_openai_conversation_responses import (
        management_configuration_guidance,
        management_ui,
    )
    from tests.test_function_repair_configuration_contract import (
        _FakeConfigEntries,
        _repairable_agent,
    )

    entry, subentry = _repairable_agent()
    subentry.data.update(chat_model="gpt-4.1", extension_data={"version": 7})
    raw = deepcopy(subentry.data)
    hass = SimpleNamespace(data={}, config_entries=_FakeConfigEntries())
    monkeypatch.setattr(
        management_ui, "entry_and_agent", lambda *_args, **_kwargs: (entry, subentry)
    )
    from custom_components.extended_openai_conversation_responses.request_rules import RequestRules
    from tests.test_request_rules import MemoryStore
    rules = RequestRules(MemoryStore())
    await rules.async_initialize()
    async def retained_rules(*_args):
        return rules
    monkeypatch.setattr(management_ui, "async_get_request_rules", retained_rules)
    monkeypatch.setattr(
        management_configuration_guidance,
        "exposed_attribute_catalog",
        lambda *_args: {},
    )
    result = await management_ui.async_management_command(
        hass,
        "admin",
        True,
        {
            "section": "function_repair",
            "action": "configuration_save",
            "entry_id": entry.entry_id,
            "subentry_id": subentry.subentry_id,
            "config": {"max_tokens": 700},
        },
    )
    assert result["valid"]
    assert subentry.data["functions"] == raw["functions"]
    assert subentry.data["function_groups"] == raw["function_groups"]
    assert subentry.data["extension_data"] == raw["extension_data"]
    assert "reasoning_effort" not in subentry.data
    before = build_provider_request_snapshot(
        {**raw, "functions": []}, {}, tools_required=False
    ).api_kwargs
    after = build_provider_request_snapshot(
        {**subentry.data, "functions": []}, {}, tools_required=False
    ).api_kwargs
    for key in ("temperature", "top_p"):
        assert key not in before
        assert key not in subentry.data
        assert key not in after
    assert subentry.data == {**raw, "max_tokens": 700}


def test_model_change_removes_unsupported_reasoning_from_storage_and_requests():
    raw = {
        "chat_model": "gpt-5.2",
        "reasoning_effort": "high",
        "functions": [],
        "temperature": 0.2,
    }
    updates = {"chat_model": "gpt-4.1"}
    saved = project_agent_config_updates(raw, updates, merge_agent_config(raw, updates))
    assert "reasoning_effort" not in saved
    request = build_provider_request_snapshot(
        saved, {}, tools_required=False
    ).api_kwargs
    assert "reasoning_effort" not in request and "reasoning" not in request
    assert request["temperature"] == saved["temperature"] == 0.2


def test_explicit_reasoning_on_model_change_overrides_the_recommendation():
    raw = {"chat_model": "gpt-5.2", "reasoning_effort": "low", "functions": []}
    updates = {"chat_model": "gpt-5.6", "reasoning_effort": "high"}
    saved = project_agent_config_updates(raw, updates, merge_agent_config(raw, updates))
    assert saved["reasoning_effort"] == "high"


async def test_import_preview_preserves_reasoning_omission_but_displays_guest_defaults(
    hass, monkeypatch
):
    from custom_components.extended_openai_conversation_responses import management_ui
    from tests.test_management_ui import _entry_pair

    entry, subentry = _entry_pair()
    raw = {"chat_model": "gpt-5.2", "functions": []}
    document = _export_agent(SimpleNamespace(data=raw, title="Preview"))
    parsed = _parse_import_document(document)
    assert GUEST_V2_FIELDS.isdisjoint(parsed["config"])
    monkeypatch.setattr(
        management_ui, "entry_and_agent", lambda *_args, **_kwargs: (entry, subentry)
    )
    result = await management_ui.async_management_command(
        hass,
        "admin",
        True,
        {
            "section": "configuration",
            "action": "import_preview",
            "mode": "new",
            "document": document,
            "entry_id": entry.entry_id,
            "subentry_id": subentry.subentry_id,
        },
    )
    assert result["valid"]
    assert "reasoning_effort" not in result["config"]
    assert result["config"].keys() >= GUEST_V2_FIELDS
    assert parsed["config"] == _parse_import_document(document)["config"]
