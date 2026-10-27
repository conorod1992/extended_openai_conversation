"""Browser startup cannot consume the native bridge's idle authenticated lifetime."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from tests_real_ha import test_browser_backend_acceptance as browser


@pytest.mark.parametrize("disconnect_after_first_command", [False, True])
async def test_browser_bridge_authenticates_on_first_use_without_replay(
    monkeypatch, disconnect_after_first_command
):
    hass, factory = object(), object()
    created = []
    sends = []
    responses = []
    bridge = None
    runner = SimpleNamespace(cleanup=AsyncMock())

    class Socket:
        closed = False
        _eoai_test_hass = hass

        async def send_json_auto_id(self, message):
            sends.append(message)
            if self.closed:
                raise ConnectionResetError("Cannot write to closing transport")

        async def receive_json(self):
            response = {"success": True, "result": {"revision": "acknowledged"}}
            responses.append(response)
            return response

    async def authenticate(selected_hass, selected_factory):
        assert selected_hass is hass and selected_factory is factory
        socket = Socket()
        created.append(socket)
        return socket

    async def start_bridge(client):
        nonlocal bridge
        bridge = client
        # Configuration update/duplicate/import still drains native HA callbacks.
        assert client._eoai_test_hass is hass
        return runner, "http://fixture.invalid/callws"

    first = {"section": "configuration", "action": "update"}
    second = {"section": "tools", "action": "set_enabled"}

    async def run_browser(**kwargs):
        # Deterministically expire any socket created before browser work begins;
        # no sleeps, heartbeat changes or retries are needed to model cold startup.
        for socket in created:
            socket.closed = True
        await bridge.send_json_auto_id(first)
        assert await bridge.receive_json() == {
            "success": True,
            "result": {"revision": "acknowledged"},
        }
        if disconnect_after_first_command:
            created[0].closed = True
        await bridge.send_json_auto_id(second)
        assert await bridge.receive_json() == {
            "success": True,
            "result": {"revision": "acknowledged"},
        }

    auth = AsyncMock(side_effect=authenticate)
    setup = AsyncMock()
    monkeypatch.setattr(browser, "_entry", Mock(return_value=object()))
    monkeypatch.setattr(browser, "_setup_entry", setup)
    monkeypatch.setattr(browser, "_admin_client", auth)
    monkeypatch.setattr(browser, "_start_ws_bridge", start_bridge)
    monkeypatch.setattr(browser, "_run_playwright", run_browser)
    if disconnect_after_first_command:
        with pytest.raises(ConnectionResetError, match="closing transport"):
            await browser.test_shipped_browser_frontend_talks_to_real_management_websocket(
                hass, factory
            )
    else:
        await browser.test_shipped_browser_frontend_talks_to_real_management_websocket(
            hass, factory
        )
    assert sends == [first, second]
    assert len(responses) == (1 if disconnect_after_first_command else 2)
    assert len(created) == 1
    auth.assert_awaited_once_with(hass, factory)
    assert setup.await_count == 2
    runner.cleanup.assert_awaited_once()
