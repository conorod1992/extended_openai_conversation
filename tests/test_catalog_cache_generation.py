"""Catalogue generations accompany every effective Management projection."""

import inspect
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from custom_components.extended_openai_conversation_responses import (
    management_ui,
    model_catalog,
    model_catalog_manager,
)


async def test_catalogue_status_is_lightweight_and_reports_active_generation(
    hass, monkeypatch
):
    manager = model_catalog_manager.ModelCatalogManager(hass)
    hass.data[model_catalog_manager.DATA_MANAGER] = manager
    connection = SimpleNamespace(send_result=Mock(), send_error=Mock())
    metadata = Mock(
        side_effect=AssertionError("Status must not build model projections")
    )
    monkeypatch.setattr(model_catalog_manager, "model_metadata", metadata)
    await inspect.unwrap(model_catalog_manager.websocket_catalog)(
        hass, connection, {"id": 1, "action": "status"}
    )
    connection.send_result.assert_called_once_with(1, manager.status())
    assert manager.status()["catalog_generation"] == model_catalog.catalog_generation()
    connection.send_error.assert_not_called()
    metadata.assert_not_called()


@pytest.mark.parametrize("action", ["get", "retention_get", "validate"])
async def test_effective_projection_generation_changes_without_configuration_revision(
    hass, management_agent, management_message, action
):
    message = management_message("configuration", action)
    if action == "validate":
        message["config"] = {"api_mode": "responses"}
    before = await management_ui.async_management_command(hass, "admin", True, message)
    generation = model_catalog.catalog_generation()
    assert before["catalog_generation"] == generation
    assert before["catalog_version"] == model_catalog.active_catalog_version()
    model_catalog.activate_catalog(None)
    try:
        after = await management_ui.async_management_command(
            hass, "admin", True, message
        )
        assert after["catalog_generation"] > generation
        assert after["config"] == before["config"]
        assert after.get("revision") == before.get("revision")
    finally:
        model_catalog.activate_catalog(None)
