"""Provider context and sharing dependency contracts across public operations."""

from copy import deepcopy
from datetime import date
import json
from types import SimpleNamespace

import pytest
import yaml

from custom_components.extended_openai_conversation_responses import management_ui as ui
from custom_components.extended_openai_conversation_responses.agent_config import (
    agent_config_snapshot,
    normalize_agent_config,
)
from custom_components.extended_openai_conversation_responses.request import (
    build_provider_request_snapshot,
)
from tests.test_management_ui import _entry_pair, _message, retained_rules  # noqa: F401
from tests.test_skill_availability import _group, _loader


@pytest.mark.parametrize("cause", ["typed", "unsafe_group", "healthy", "on_demand"])
@pytest.mark.parametrize("kind", ["configuration", "setup"])
def test_reviewed_export_reconciles_skill_dependencies(hass, cause, kind):
    tools = [_loader()]
    groups = []
    if cause == "typed":
        tools.append(
            {
                "spec": {
                    "name": "announce_holiday",
                    "parameters": {"type": "object", "properties": {}},
                },
                "function": {
                    "type": "script",
                    "sequence": [{"variables": {"holiday_date": date(2026, 12, 25)}}],
                },
            }
        )
    elif cause in {"unsafe_group", "on_demand"}:
        groups = [_group()]
        if cause == "unsafe_group":
            groups[0]["description"] = (
                "Authorization: Bearer sk-FABRICATED123456789012345"
            )
    config = {
        "functions": yaml.safe_dump(tools),
        "function_groups": groups,
        "skills": ["calendar"],
    }
    normalize_agent_config(config)
    original = deepcopy(config)
    if kind == "configuration":
        artifact = ui._export_agent(SimpleNamespace(data=config), reviewed=True)
    else:
        from tests.test_reviewed_quarantine_export import setup_roundtrip

        result, _restored = setup_roundtrip(config, reviewed=True)
        artifact = {
            "schema": "extended_openai_conversation.agent",
            "version": 2,
            "title": "Shared",
            "config": result["document"]["sections"]["configuration"],
            "security": result["security"],
        }
    restored = ui._parse_import_document(json.dumps(artifact))["config"]
    unavailable = cause in {"typed", "unsafe_group"}
    assert restored["skills"] == ([] if unavailable else ["calendar"])
    assert (
        any(item["field"] == "skills" for item in artifact["security"]["omitted"])
        is unavailable
    )
    if cause == "unsafe_group":
        assert (
            next(
                tool
                for tool in yaml.safe_load(restored["functions"])
                if tool["spec"]["name"] == "load_skill"
            )["enabled"]
            is False
        )
    assert config == original


@pytest.mark.parametrize("identity", ["o3-pro", "o3-pro-2025-06-10"])
@pytest.mark.parametrize("bound", [False, True])
@pytest.mark.parametrize("provider", ["azure", "openai"])
async def test_management_preserves_parent_provider_reasoning(
    hass, identity, bound, provider
):
    entry, child = _entry_pair()
    entry.data = {"api_provider": provider}
    child.data.update(
        chat_model=identity, api_mode="responses", reasoning_effort="high"
    )
    child.data.pop("azure_model", None)
    if bound and provider == "azure":
        child.data["azure_model"] = identity
    hass.config_entries.async_get_entry.return_value = entry
    hass.config_entries.async_entries.return_value = [entry]
    saved = deepcopy(child.data)

    async def command(action, **fields):
        return await ui.async_management_command(
            hass,
            "admin",
            True,
            {
                **_message("configuration", action, **fields),
                "entry_id": entry.entry_id,
                "subentry_id": child.subentry_id,
            },
        )

    result = await command("get")
    assert result["model_capabilities"]["reasoning"]["supported"] is (
        provider == "azure"
    )
    assert result["config"].get("reasoning_effort") == (
        "high" if provider == "azure" else None
    )
    assert result["api_provider"] == provider
    await command("duplicate")
    duplicate = hass.config_entries.async_add_subentry.call_args.args[1]
    exported = await command("export", reviewed_custom=True)
    imported = await command("import_preview", document=exported["json"], mode="new")
    for config in (duplicate.data, imported["config"]):
        sent = build_provider_request_snapshot(
            dict(config), entry.data, tools_required=False
        ).api_kwargs
        assert sent.get("reasoning") == (
            {"effort": "high"} if provider == "azure" else None
        )
    validated = await command("validate", config={"reasoning_effort": "medium"})
    assert validated["valid"], validated
    assert validated["config"].get("reasoning_effort") == (
        "medium" if provider == "azure" else None
    )
    assert child.data == saved

    def apply_update(parent, current, *, data, **kwargs):
        assert parent is entry
        current.data = data

    hass.config_entries.async_update_subentry.side_effect = apply_update
    result = await command("get")
    await command(
        "update", config={"reasoning_effort": "medium"}, revision=result["revision"]
    )
    assert child.data.get("reasoning_effort") == (
        "medium" if provider == "azure" else None
    )
    result = await command("get")
    await command("save", config={"max_tokens": 1234}, revision=result["revision"])
    sent = build_provider_request_snapshot(
        child.data, entry.data, tools_required=False
    ).api_kwargs
    assert sent.get("reasoning") == (
        {"effort": "medium"} if provider == "azure" else None
    )


def test_portable_validation_preserves_provider_specific_explicit_effort(hass):
    source = {
        "chat_model": "o3-pro",
        "api_mode": "responses",
        "reasoning_effort": "high",
    }
    assert agent_config_snapshot(source)["reasoning_effort"] == "high"
    assert "reasoning_effort" not in agent_config_snapshot(source, provider="openai")
    assert agent_config_snapshot(source, provider="azure")["reasoning_effort"] == "high"


async def test_cached_configuration_tracks_provider_replacement(hass):
    entry, child = _entry_pair()
    child.data.update(
        chat_model="o3-pro", api_mode="responses", reasoning_effort="high"
    )
    child.data.pop("azure_model", None)
    hass.config_entries.async_get_entry.return_value = entry
    hass.config_entries.async_entries.return_value = [entry]
    message = {
        **_message("configuration", "get"),
        "entry_id": entry.entry_id,
        "subentry_id": child.subentry_id,
    }
    revisions = []
    for provider in ("azure", "openai", "azure"):
        entry.data = {"api_provider": provider}
        result = await ui.async_management_command(hass, "admin", True, message)
        assert result["model_capabilities"]["reasoning"]["supported"] is (
            provider == "azure"
        )
        assert result["config"].get("reasoning_effort") == (
            "high" if provider == "azure" else None
        )
        revisions.append(result["revision"])
    assert len(set(revisions)) == 1
    assert child.data["reasoning_effort"] == "high"


@pytest.mark.parametrize("section", ["guest_mode", "knowledge"])
@pytest.mark.parametrize("effort", [None, "high"])
async def test_specialized_save_seeds_the_actual_provider_context(
    hass, monkeypatch, section, effort
):
    from unittest.mock import AsyncMock

    entry, child = _entry_pair()
    entry.data = {"api_provider": "azure"}
    child.data.update(chat_model="o3-pro", api_mode="responses")
    child.data.pop("azure_model", None)
    child.data.pop("reasoning_effort", None)
    if effort:
        child.data["reasoning_effort"] = effort
    hass.config_entries.async_get_entry.return_value = entry
    hass.config_entries.async_entries.return_value = [entry]

    def apply_update(parent, current, *, data, **kwargs):
        current.data = data

    hass.config_entries.async_update_subentry.side_effect = apply_update
    monkeypatch.setattr(
        ui,
        "async_get_knowledge",
        AsyncMock(return_value=SimpleNamespace(total_source_count=0)),
    )

    async def command(section, action, **fields):
        return await ui.async_management_command(
            hass,
            "admin",
            True,
            {
                **_message(section, action, **fields),
                "entry_id": entry.entry_id,
                "subentry_id": child.subentry_id,
            },
        )

    before = await command("configuration", "get")
    fields = (
        {"config": {"guest_mode_enabled": True}}
        if section == "guest_mode"
        else {"enabled": True}
    )
    await command(
        section,
        "save_policy" if section == "guest_mode" else "set_enabled",
        revision=before["revision"],
        **fields,
    )
    cached = await command("configuration", "get")
    assert cached["_performance"]["snapshot_cache_hit"]
    assert cached["config"]["reasoning_effort"] == (effort or "low")
    assert child.data.get("reasoning_effort") == effort
