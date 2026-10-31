"""Real queue admission and HA serialization feed the checked JS consumer."""

import os
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.extended_openai_conversation_responses import intercom_panel
from homeassistant.components.websocket_api.messages import message_to_json_bytes
from homeassistant.core import Context
from tests.test_broadcast_admission_contract import admission  # noqa: F401


@pytest.mark.parametrize("contract", ["current", "legacy"])
async def test_python_admission_reaches_checked_consumer(
    hass, admission, monkeypatch, contract  # noqa: F811 - imported pytest fixture
):
    node = shutil.which("node")
    if node is None:
        if os.environ.get("RUN_REAL_HA_BROWSER") == "1":
            pytest.fail("Genuine frontend CI requires Node for the wire contract")
        pytest.skip("Node is required for the producer-to-consumer contract")
    manager, _, _, _ = admission
    monkeypatch.setattr(
        intercom_panel, "async_get_intercom", AsyncMock(return_value=manager)
    )
    connection = SimpleNamespace(
        user=SimpleNamespace(is_admin=True),
        context=lambda _: Context(user_id="owner"),
        send_result=Mock(),
        send_error=Mock(),
    )
    await intercom_panel.websocket_broadcast.__wrapped__(
        hass,
        connection,
        {"id": 1, "action": "send", "message": "Dinner", "whole_home": True},
    )
    connection.send_error.assert_not_called()
    connection.send_result.assert_called_once()
    response = connection.send_result.call_args.args[1]
    assert len(manager.history()) == 1
    assert sum(len(queue) for queue in manager._queues.values()) == 1
    payload = message_to_json_bytes(
        {"id": 1, "type": "result", "success": True, "result": response}
    )
    runner = Path(__file__).with_name("broadcast-python-contract-runner.mjs")
    result = subprocess.run(
        [node, str(runner), contract],
        input=payload,
        capture_output=True,
        timeout=30,
        check=False,
    )
    if contract == "legacy":
        assert result.returncode != 0
        assert b"did not confirm queuing Broadcast" in result.stderr
    else:
        assert result.returncode == 0, result.stderr.decode()
