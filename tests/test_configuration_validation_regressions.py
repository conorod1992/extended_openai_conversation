"""Saved-request validation and catalogue generation regressions."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.extended_openai_conversation_responses import (
    config_flow,
    management_ui,
    model_catalog,
    model_catalog_manager,
    services,
)
from custom_components.extended_openai_conversation_responses.const import (
    DEFAULT_AI_TASK_OPTIONS,
    DOMAIN,
)
from custom_components.extended_openai_conversation_responses.live_subentry_updates import (
    is_live_subentry_update,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import HomeAssistantError
from tests.test_management_ui import (
    _residual_entry_pair,
    _residual_hass,
    _residual_message,
)
from tests.test_model_catalog_transitions import MemoryStore
from tests.test_service_handlers import _call, _handlers


@pytest.fixture(autouse=True)
def isolate_catalog():
    model_catalog.activate_catalog(None)
    yield
    model_catalog.activate_catalog(None)


def expanded_catalog():
    candidate = deepcopy(model_catalog.BUNDLED_CATALOG)
    candidate["catalog_version"] += 1
    model = next(item for item in candidate["models"] if item["id"] == "gpt-4o")
    model["limits"]["max_output_tokens"] = 32768
    return candidate


def test_management_capability_cache_tracks_catalog_activation_and_reset():
    initial = management_ui._configuration_model_capabilities("gpt-4o")
    model_catalog.activate_catalog(expanded_catalog())
    changed = management_ui._configuration_model_capabilities("gpt-4o")
    assert changed != initial
    model_catalog.activate_catalog(None)
    assert management_ui._configuration_model_capabilities("gpt-4o") == initial


async def test_catalog_reset_preserves_saved_output_limit(hass):
    candidate = expanded_catalog()
    model_catalog.activate_catalog(candidate)
    manager = model_catalog_manager.ModelCatalogManager(hass)
    manager.store = MemoryStore()
    manager.catalog = candidate
    subentry = SimpleNamespace(
        subentry_type="ai_task_data",
        subentry_id="agent",
        data={"chat_model": "gpt-4o", "max_tokens": 20000},
    )
    hass.config_entries.async_entries.return_value = [
        SimpleNamespace(entry_id="entry", data={}, subentries={"agent": subentry})
    ]
    assert await manager._candidate_preserves_saved_requests(candidate)
    outcome = await manager.async_reset()
    assert "blocked" in outcome["last_error"]
    assert manager.catalog == candidate
    assert manager.store.save_calls == 0


@pytest.mark.parametrize("advanced", [False, True])
@pytest.mark.parametrize(
    "options,provider",
    [
        ({"api_mode": "chat_completions", "web_search": True}, {}),
        ({"chat_model": "gpt-4o", "max_tokens": 9999999}, {}),
        ({"chat_model": "gpt-5-pro", "api_mode": "chat_completions"}, {}),
        (
            {"api_mode": "responses", "web_search": True},
            {"base_url": "https://custom.example/v1"},
        ),
    ],
)
async def test_ai_task_native_flow_rejects_invalid_candidate(
    options, provider, advanced
):
    handler = SimpleNamespace(
        options=dict(DEFAULT_AI_TASK_OPTIONS),
        _is_new=True,
        _temp_data=options,
        _get_entry=lambda: SimpleNamespace(
            state=ConfigEntryState.LOADED, data=provider
        ),
        async_create_entry=Mock(),
        async_update_and_abort=Mock(),
        async_show_form=Mock(side_effect=lambda **kwargs: kwargs),
        add_suggested_values_to_schema=lambda schema, values: schema,
    )
    if advanced:
        result = await config_flow.ExtendedOpenAIAITaskSubentryFlowHandler.async_step_advanced(
            handler, {}
        )
    else:
        result = (
            await config_flow.ExtendedOpenAIAITaskSubentryFlowHandler.async_step_init(
                handler, options
            )
        )
    assert result["errors"] == {"base": "invalid_request"}
    assert result["description_placeholders"]["reason"]
    handler.async_create_entry.assert_not_called()
    handler.async_update_and_abort.assert_not_called()


@pytest.mark.parametrize("action", ["import_preview", "import"])
async def test_setup_import_rejects_provider_incompatibility(monkeypatch, action):
    entry, subentry = _residual_entry_pair()
    hass = _residual_hass(entry)
    monkeypatch.setattr(
        management_ui, "entry_and_agent", lambda *_args: (entry, subentry)
    )
    document = management_ui._export_agent(subentry)
    document["config"].update({"api_mode": "chat_completions", "web_search": True})
    with pytest.raises(HomeAssistantError):
        await management_ui.async_management_command(
            hass,
            "admin",
            True,
            _residual_message("configuration", action, document=document, confirm=True),
        )
    hass.config_entries.async_update_subentry.assert_not_called()
    hass.config_entries.async_add_subentry.assert_not_called()


async def test_legacy_configuration_model_update_uses_reload_boundary(monkeypatch):
    entry, subentry = _residual_entry_pair()
    hass = _residual_hass(entry)
    monkeypatch.setattr(
        management_ui, "entry_and_agent", lambda *_args: (entry, subentry)
    )
    monkeypatch.setattr(
        management_ui,
        "decorate_configuration_result",
        lambda _hass, _entry_data, result, **_kwargs: result,
    )
    observed = []
    hass.config_entries.async_update_subentry.side_effect = lambda *_args, **_kwargs: (
        observed.append(is_live_subentry_update())
    )
    await management_ui.async_management_command(
        hass,
        "admin",
        True,
        _residual_message("configuration", "update", config={"chat_model": "gpt-4o"}),
    )
    assert observed == [False]


@pytest.mark.parametrize(
    "settings", [{"chat_model": "gpt-4o"}, {"web_search": True}, {"functions": []}]
)
def test_settings_writer_rejects_configuration_fields(settings):
    with pytest.raises(HomeAssistantError, match="Unknown settings"):
        management_ui._validate_settings(settings)


@pytest.mark.parametrize("revision", [None, "stale"])
async def test_settings_writer_requires_current_revision(monkeypatch, revision):
    entry, subentry = _residual_entry_pair()
    hass = _residual_hass(entry)
    monkeypatch.setattr(
        management_ui, "entry_and_agent", lambda *_args: (entry, subentry)
    )
    with pytest.raises(HomeAssistantError, match=r"revision|another tab"):
        await management_ui.async_management_command(
            hass,
            "admin",
            True,
            _residual_message(
                "settings",
                "update",
                settings={"archive_enabled": True},
                revision=revision,
            ),
        )
    hass.config_entries.async_update_subentry.assert_not_called()


@pytest.mark.parametrize("kind", ["conversation", "ai_task_data"])
async def test_provider_change_rejects_incompatible_child(hass, monkeypatch, kind):
    child = SimpleNamespace(
        subentry_type=kind,
        subentry_id="agent",
        title="Search agent",
        data={"chat_model": "gpt-5.6", "web_search": True, "api_mode": "responses"},
    )
    entry = SimpleNamespace(
        entry_id="entry",
        domain=DOMAIN,
        data={"api_key": "old"},
        subentries={"agent": child},
    )
    hass.config_entries.async_get_entry.return_value = entry
    monkeypatch.setattr(services, "get_authenticated_client", AsyncMock())
    handlers = await _handlers(hass)
    with pytest.raises(
        HomeAssistantError, match="incompatible with agent Search agent"
    ):
        await handlers["change_config"](
            _call({"config_entry": "entry", "base_url": "https://custom.example/v1"})
        )
    hass.config_entries.async_update_entry.assert_not_called()
