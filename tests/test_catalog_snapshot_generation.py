"""Catalogue-derived Management snapshots follow activation without durable edits."""

from copy import deepcopy
from dataclasses import replace
from types import MappingProxyType
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import (
    management_function_repair as repair,
    management_ui as ui,
    model_catalog as catalog,
    model_catalog_manager as runtime,
)
from custom_components.extended_openai_conversation_responses.request import (
    build_provider_request_snapshot,
)
from homeassistant.config_entries import ConfigSubentry
from tests.test_management_loading_performance import _hass_with_agent
from tests.test_model_catalog_transitions import MemoryStore
from tests.test_request_rules import manager


@pytest.fixture(autouse=True)
def isolated_catalog():
    catalog.activate_catalog(None)
    yield
    catalog.activate_catalog(None)


@pytest.mark.parametrize("explicit", [False, True])
async def test_public_import_read_apply_read_reset(monkeypatch, explicit, hass):
    hass, entry, original = _hass_with_agent()
    subentry = ConfigSubentry(
        data=MappingProxyType(original.data),
        subentry_type="conversation",
        title="Agent",
        unique_id=None,
    )
    entry.subentries = {subentry.subentry_id: subentry}

    def update(_entry, previous, *, data, title=None):
        entry.subentries[previous.subentry_id] = replace(
            previous, data=MappingProxyType(data), title=title or previous.title
        )
        hass.config_entries.updates += 1

    hass.config_entries.async_update_subentry = update
    rules = await manager()
    for module in (ui, runtime):
        monkeypatch.setattr(
            module, "async_get_request_rules", AsyncMock(return_value=rules)
        )
    monkeypatch.setattr(runtime, "sync_all_model_lifecycles", lambda _hass: None)
    monkeypatch.setattr(
        ui, "decorate_configuration_result", lambda _hass, _data, result, **kw: result
    )
    message = {
        "entry_id": entry.entry_id,
        "subentry_id": subentry.subentry_id,
        "section": "configuration",
        "action": "get",
    }
    config = {"chat_model": "gpt-5.6"}
    if explicit:
        config["reasoning_effort"] = "medium"
    imported = await ui.async_management_command(
        hass,
        "admin",
        True,
        {
            **message,
            "action": "import",
            "confirm": True,
            "document": {
                "schema": "extended_openai_conversation.agent",
                "version": ui.AGENT_CONFIG_EXPORT_VERSION,
                "title": "Imported",
                "config": config,
            },
        },
    )
    subentry = entry.subentries[subentry.subentry_id]
    saved = dict(subentry.data)
    update_count = hass.config_entries.updates
    monkeypatch.setattr(
        runtime, "PropagatingWriteStore", lambda *a, **kw: MemoryStore()
    )
    catalogue = runtime.ModelCatalogManager(hass)
    catalogue.store = MemoryStore()
    candidate = deepcopy(catalog.BUNDLED_CATALOG)
    candidate["catalog_version"] += 1
    model = next(model for model in candidate["models"] if model["id"] == "gpt-5.6")
    model["recommended_profile"]["reasoning_effort"] = "high"

    async def check(expected):
        result = await ui.async_management_command(hass, "admin", True, message)
        request = build_provider_request_snapshot(subentry.data, entry.data)
        effort = request.api_kwargs.get("reasoning_effort")
        if effort is None:
            effort = request.api_kwargs.get("reasoning", {}).get("effort")
        assert result["config"]["reasoning_effort"] == effort == expected
        assert result["revision"] == imported["revision"]
        assert dict(subentry.data) == saved
        assert hass.config_entries.updates == update_count

    await check("medium" if explicit else "low")
    catalogue.available_catalog = candidate
    await catalogue.async_apply_update()
    assert catalogue.last_error is None
    await check("medium" if explicit else "high")
    await check("medium" if explicit else "high")
    await catalogue.async_reset()
    assert catalogue.last_error is None
    await check("medium" if explicit else "low")
    assert ("reasoning_effort" in subentry.data) is explicit


@pytest.mark.parametrize("seeded", [False, True])
def test_retained_projection_refreshes_without_revision_change(seeded, hass):
    _fake_hass, entry, subentry = _hass_with_agent()
    subentry.data = {**subentry.data, "chat_model": "gpt-5.6"}
    subentry.data.pop("reasoning_effort", None)
    projection = repair.persisted_config_projection(subentry)
    before, _ = repair.normalized_persisted_config_snapshot(projection)
    if seeded:
        repair.seed_persisted_config_projection(
            entry, subentry, before, projection.revision
        )
        projection = repair.persisted_config_projection(subentry)
    revision = projection.revision
    candidate = deepcopy(catalog.BUNDLED_CATALOG)
    model = next(model for model in candidate["models"] if model["id"] == "gpt-5.6")
    model["recommended_profile"]["reasoning_effort"] = "high"
    catalog.activate_catalog(candidate)
    after, hit = repair.normalized_persisted_config_snapshot(projection)
    assert not hit and after["reasoning_effort"] == "high"
    assert projection.revision == revision
    # Removing the override restores the bundled model's recommendation.
    candidate["models"].remove(model)
    catalog.activate_catalog(candidate)
    restored, hit = repair.normalized_persisted_config_snapshot(projection)
    assert not hit and restored["reasoning_effort"] == "low"
    assert projection.revision == revision


async def test_repair_snapshot_refreshes_catalogue_defaults(hass):
    from tests.test_management_function_repair import _mixed_legacy_tool_data

    fake_hass, entry, subentry = _hass_with_agent()
    invalid, _tools, _valid = _mixed_legacy_tool_data()
    subentry.data = {**subentry.data, **invalid, "chat_model": "gpt-5.6"}
    subentry.data.pop("reasoning_effort", None)
    projection = repair.persisted_config_projection(subentry)
    before = repair.safe_configuration_payload(
        fake_hass, entry, subentry, projection=projection
    )
    candidate = deepcopy(catalog.BUNDLED_CATALOG)
    model = next(model for model in candidate["models"] if model["id"] == "gpt-5.6")
    model["recommended_profile"]["reasoning_effort"] = "high"
    catalog.activate_catalog(candidate)
    after = repair.safe_configuration_payload(
        fake_hass, entry, subentry, projection=projection
    )
    assert before["config"]["reasoning_effort"] == "low"
    assert after["config"]["reasoning_effort"] == "high"
    assert before["revision"] == after["revision"]
    assert before["function_repair"] == after["function_repair"]
