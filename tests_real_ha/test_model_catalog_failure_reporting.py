"""Manual model catalogue failure reporting through genuine HA WebSocket."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from pytest_homeassistant_custom_component.common import MockUser

from custom_components.extended_openai_conversation_responses import (
    model_catalog_manager as runtime,
)
from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry
from tests_real_ha.test_backup_transfer_protocol import _user_token


async def test_manual_update_failure_returns_websocket_error_and_keeps_current_data(
    hass, hass_ws_client, monkeypatch
):
    entry = _make_entry(include_ai_task=False)
    await _setup_entry(hass, entry)
    admin = await hass_ws_client(
        hass, await _user_token(hass, MockUser(id="catalog-admin-failure", is_owner=True))
    )

    context = AsyncMock()
    context.__aenter__.side_effect = TimeoutError()
    get = Mock(return_value=context)
    monkeypatch.setattr(
        runtime, "async_get_clientsession", lambda _: SimpleNamespace(get=get)
    )

    await admin.send_json_auto_id(
        {"type": runtime.WS_CATALOG, "action": "update", "model": "gpt-5.6"}
    )
    result = await admin.receive_json()

    assert result["success"] is False
    assert result["error"]["code"] == "model_catalog_update_failed"
    assert "current catalogue was kept" in result["error"]["message"]
    assert get.call_count == 1

    manager = hass.data[runtime.DATA_MANAGER]
    assert manager.status()["source"] == "bundled"
    assert manager.status()["last_error"]

    # A failed manual refresh is still just a failed refresh: the existing model
    # catalogue remains usable immediately through the same registered WS command.
    await admin.send_json_auto_id(
        {"type": runtime.WS_CATALOG, "action": "lookup", "model": "gpt-5.6"}
    )
    lookup = await admin.receive_json()
    assert lookup["success"] is True
    assert lookup["result"]["source"] == "bundled"
    assert lookup["result"]["model_capabilities"]
