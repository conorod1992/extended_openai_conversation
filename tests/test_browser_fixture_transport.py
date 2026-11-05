"""Browser startup cannot consume the native bridge's idle authenticated lifetime."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from tests_real_ha import test_browser_backend_acceptance as browser
from tests_stress import test_browser_management_contract as nightly


@pytest.mark.parametrize("disconnect_after_first_command", [False, True])
@pytest.mark.parametrize("entrypoint", ["ordinary", "nightly"])
async def test_browser_bridge_authenticates_on_first_use_without_replay(
    monkeypatch, disconnect_after_first_command, entrypoint
):
    hass, factory = object(), object()
    created = []
    sends = []
    responses = []
    browser_runs = 0
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
        nonlocal browser_runs
        # Deterministically expire any socket created before browser work begins;
        # no sleeps, heartbeat changes or retries are needed to model cold startup.
        if browser_runs == 0:
            for socket in created:
                socket.closed = True
        browser_runs += 1
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
    monkeypatch.setattr(nightly, "_entry", Mock(return_value=object()))
    monkeypatch.setattr(nightly, "_setup_entry", setup)
    monkeypatch.setattr(nightly, "_start_ws_bridge", start_bridge)
    monkeypatch.setattr(nightly, "_run_playwright", run_browser)
    target = (
        browser.test_shipped_browser_frontend_talks_to_real_management_websocket
        if entrypoint == "ordinary"
        else nightly.test_nightly_management_mutations_cross_real_websocket
    )
    if disconnect_after_first_command:
        with pytest.raises(ConnectionResetError, match="closing transport"):
            await target(hass, factory)
    else:
        await target(hass, factory)
    expected_runs = (
        2 if entrypoint == "nightly" and not disconnect_after_first_command else 1
    )
    assert browser_runs == expected_runs
    assert sends == [first, second] * expected_runs
    assert len(responses) == (
        1 if disconnect_after_first_command else 2 * expected_runs
    )
    assert len(created) == 1
    auth.assert_awaited_once_with(hass, factory)
    assert setup.await_count == (2 if entrypoint == "ordinary" else 1)
    runner.cleanup.assert_awaited_once()
