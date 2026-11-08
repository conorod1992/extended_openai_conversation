"""The parallel startup path must still fail closed before UI testing."""

import json
from unittest.mock import Mock

import pytest

from ci import ios_companion_runtime as runtime


def test_simulator_selection_uses_newest_ios_and_correct_model():
    devices = {
        "devices": {
            "iOS-26-4": [{"name": "iPhone 17", "udid": "old"}],
            "iOS-27-0": [
                {"name": "iPhone 17", "udid": "new"},
                {"name": "iPhone 18", "udid": "wrong"},
            ],
            "iOS-28-0": [
                {"name": "iPhone 17", "udid": "unavailable", "isAvailable": False}
            ],
            "watchOS-30-0": [{"name": "iPhone 17", "udid": "watch"}],
        }
    }
    assert runtime.newest_phone(devices) == "new"
    with pytest.raises(RuntimeError, match="No available"):
        runtime.newest_phone({"devices": {}})


def test_start_erases_once_then_boots_asynchronously(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setenv("GITHUB_ENV", str(tmp_path / "env"))
    monkeypatch.setattr(
        runtime.subprocess,
        "check_output",
        lambda *a, **kw: json.dumps(
            {"devices": {"iOS-27-0": [{"name": "iPhone 17", "udid": "phone"}]}}
        ),
    )
    run, background = Mock(), Mock()
    monkeypatch.setattr(runtime.subprocess, "run", run)
    monkeypatch.setattr(runtime.subprocess, "Popen", background)
    runtime.start_simulator()
    assert [call.args[0][2] for call in run.call_args_list] == [
        "shutdown",
        "erase",
        "boot",
    ]
    assert run.call_args_list[1].kwargs["check"] is True
    assert background.call_args.args[0] == [
        "xcrun",
        "simctl",
        "bootstatus",
        "phone",
        "-b",
    ]
    assert "IOS_SIMULATOR_UDID=phone" in (tmp_path / "env").read_text()


def test_ha_process_death_is_not_hidden_by_ready_marker(tmp_path, monkeypatch):
    (tmp_path / "companion-ha.pid").write_text("123")
    (tmp_path / "companion-ready.json").write_text("{}")
    monkeypatch.setattr(runtime.os, "kill", Mock(side_effect=ProcessLookupError))
    request = Mock()
    monkeypatch.setattr(runtime, "urlopen", request)
    with pytest.raises(RuntimeError, match="exited"):
        runtime.wait_for_ha(tmp_path)
    request.assert_not_called()


def test_readiness_requires_http_success_and_times_out(tmp_path, monkeypatch):
    (tmp_path / "companion-ha.pid").write_text("123")
    (tmp_path / "companion-ready.json").write_text("{}")
    monkeypatch.setattr(runtime.os, "kill", Mock())
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.status = 200
    monkeypatch.setattr(runtime, "urlopen", Mock(return_value=response))
    runtime.wait_for_ha(tmp_path)
    with pytest.raises(TimeoutError, match="did not become ready"):
        runtime.wait_for_ha(tmp_path, timeout=0)
