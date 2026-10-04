"""Both catalogue writers protect the same persisted request guarantees."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses import (
    model_catalog as data,
)
from custom_components.extended_openai_conversation_responses.model_catalog_manager import (
    ModelCatalogManager,
)
from tests.test_model_catalog_transitions import MemoryStore


@pytest.fixture(autouse=True)
def isolate_catalogue():
    data.activate_catalog(None)
    yield
    data.activate_catalog(None)


@pytest.mark.parametrize("route", ["apply", "reset"])
@pytest.mark.parametrize("kind", ["conversation", "ai_task_data"])
@pytest.mark.parametrize("valid", [False, True])
async def test_catalogue_writers_share_saved_request_atomicity(
    hass, route, kind, valid
):
    expanded = deepcopy(data.BUNDLED_CATALOG)
    expanded["catalog_version"] += 1
    model = next(item for item in expanded["models"] if item["id"] == "gpt-4o")
    model["limits"]["max_output_tokens"] = 32768
    data.activate_catalog(expanded)
    manager = ModelCatalogManager(hass)
    manager.store = MemoryStore()
    manager.catalog = expanded
    candidate = deepcopy(expanded)
    candidate["catalog_version"] = expanded["catalog_version"] + 1
    manager.available_catalog = candidate
    child = SimpleNamespace(
        subentry_type=kind,
        subentry_id="child",
        title="Contract agent",
        data={
            "chat_model": "gpt-4o",
            "max_tokens": 500 if valid else (999999 if route == "apply" else 20000),
            "max_function_calls_per_conversation": 0,
            "functions": [],
        },
    )
    hass.config_entries.async_entries.return_value = [
        SimpleNamespace(entry_id="entry", data={}, subentries={"child": child})
    ]
    before = deepcopy(child.data)
    result = await (
        manager.async_apply_update() if route == "apply" else manager.async_reset()
    )
    assert child.data == before
    assert manager.store.save_calls == int(valid)
    if valid:
        assert not result["last_error"]
        assert manager.catalog == (candidate if route == "apply" else None)
    else:
        assert result["last_error"]
        assert manager.catalog == expanded
        assert manager.available_catalog == candidate
        assert data.model_metadata("gpt-4o")["limits"]["max_output_tokens"] == 32768


@pytest.mark.parametrize("kind", ["conversation", "ai_task_data"])
@pytest.mark.parametrize("search", [False, True])
async def test_provider_switch_validates_children_before_publishing(
    hass, monkeypatch, kind, search
):
    from unittest.mock import AsyncMock

    from custom_components.extended_openai_conversation_responses import services
    from custom_components.extended_openai_conversation_responses.const import DOMAIN
    from homeassistant.exceptions import HomeAssistantError
    from tests.test_service_handlers import _call, _handlers

    child = SimpleNamespace(
        subentry_type=kind,
        subentry_id="child",
        title="Contract child",
        data={
            "chat_model": "gpt-5.6",
            "api_mode": "responses",
            "web_search": search,
            "functions": [],
            "max_function_calls_per_conversation": 0,
        },
    )
    entry = SimpleNamespace(
        entry_id="entry",
        domain=DOMAIN,
        data={"api_key": "old"},
        subentries={"child": child},
    )
    hass.config_entries.async_get_entry.return_value = entry
    authenticate = AsyncMock()
    monkeypatch.setattr(services, "get_authenticated_client", authenticate)
    handlers = await _handlers(hass)
    call = _call({"config_entry": "entry", "base_url": "https://custom.example/v1"})
    if search:
        with pytest.raises(HomeAssistantError, match="incompatible with agent"):
            await handlers["change_config"](call)
        hass.config_entries.async_update_entry.assert_not_called()
    else:
        await handlers["change_config"](call)
        hass.config_entries.async_update_entry.assert_called_once()
    authenticate.assert_awaited_once()
    assert entry.data == {"api_key": "old"}
