"""Contracts for the genuine Home Assistant OS + Supervisor VM lane."""

import importlib.util
from pathlib import Path
import subprocess
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "haos-supervisor-vm-acceptance.yml"
DRIVER = ROOT / "ci" / "haos_supervisor_acceptance.py"


def _driver():
    spec = importlib.util.spec_from_file_location("haos_acceptance_driver", DRIVER)
    driver = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(driver)
    return driver


@pytest.mark.parametrize(
    "message",
    [
        "Error response from daemon: container abc is not running",
        "Error response from daemon: container abc is restarting",
        "Error response from daemon: No such container: homeassistant",
    ],
)
def test_restore_job_waits_through_core_shutdown(monkeypatch, message):
    driver = _driver()
    api = Mock(
        side_effect=[
            AssertionError(message),
            {"result": "ok", "data": {"done": False}},
            {"result": "ok", "data": {"done": True, "errors": []}},
        ]
    )
    monkeypatch.setattr(driver, "_supervisor_api", api)
    monkeypatch.setattr(driver.time, "sleep", lambda _: None)
    result = driver._wait_supervisor_job(object(), "restore-job")
    assert result == {"done": True, "errors": []}
    assert api.call_count == 3
    assert all(call.args[2] == "/jobs/restore-job" for call in api.call_args_list)


@pytest.mark.parametrize(
    "failure",
    [
        AssertionError("SSH permission denied"),
        AssertionError("Error response from daemon: No such container: unrelated"),
        {"result": "ok", "data": {"done": True, "errors": ["restore failed"]}},
    ],
)
def test_restore_job_does_not_hide_transport_or_restore_errors(monkeypatch, failure):
    driver = _driver()
    api = Mock(side_effect=[failure])
    monkeypatch.setattr(driver, "_supervisor_api", api)
    with pytest.raises(AssertionError):
        driver._wait_supervisor_job(object(), "restore-job")
    assert api.call_count == 1


@pytest.mark.parametrize("returncode", [137, 143])
def test_restore_job_resumes_when_core_terminates_an_active_poll(
    monkeypatch, returncode
):
    driver = _driver()
    interrupted = driver.SSHCommandError(
        "docker exec homeassistant python -c ...",
        subprocess.CompletedProcess([], returncode, stdout="", stderr=""),
    )
    api = Mock(side_effect=[interrupted, {"result": "ok", "data": {"done": True}}])
    monkeypatch.setattr(driver, "_supervisor_api", api)
    monkeypatch.setattr(driver.time, "sleep", lambda _: None)
    assert driver._wait_supervisor_job(object(), "restore-job") == {"done": True}
    assert api.call_count == 2


@pytest.mark.parametrize(
    "returncode,stdout", [(1, ""), (255, ""), (137, "partial JSON")]
)
def test_restore_job_rejects_other_remote_command_failures(
    monkeypatch, returncode, stdout
):
    driver = _driver()
    failure = driver.SSHCommandError(
        "docker exec homeassistant python -c ...",
        subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=""),
    )
    api = Mock(side_effect=[failure])
    monkeypatch.setattr(driver, "_supervisor_api", api)
    with pytest.raises(driver.SSHCommandError):
        driver._wait_supervisor_job(object(), "restore-job")
    assert api.call_count == 1


def test_haos_vm_workflow_runs_on_pr_weekly_and_manual() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    trigger_block = text.split("permissions:", 1)[0]

    assert 'cron: "17 6 * * 0"' in trigger_block
    assert "workflow_dispatch:" in trigger_block
    assert "pull_request:" in trigger_block
    assert "push:" not in trigger_block
    assert "release:" not in trigger_block


def test_haos_vm_workflow_boots_official_qcow2_with_supervisor_appliance_boundaries() -> (
    None
):
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "home-assistant/operating-system/releases/latest" in text
    assert 'startswith("haos_ova-")' in text
    assert ".qcow2.xz" in text
    assert "qemu-system-x86_64" in text
    assert "OVMF_CODE" in text
    assert "config.img" in text
    assert "authorized_keys" in text
    assert "usb-storage" in text
    assert "hostfwd=tcp:127.0.0.1:8123-:80" in text
    assert "hostfwd=tcp:127.0.0.1:22222-:22222" in text


def test_haos_driver_uses_public_ha_interfaces_and_supervisor_restart() -> None:
    text = DRIVER.read_text(encoding="utf-8")

    assert "/api/onboarding/users" in text
    assert "/api/config/config_entries/flow" in text
    assert '"conversation/process"' in text
    spec = importlib.util.spec_from_file_location("haos_acceptance_driver", DRIVER)
    assert spec is not None and spec.loader is not None
    driver = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(driver)
    assert driver.WS_COMMAND == "extended_openai_conversation_responses/management"
    assert "await ws.call(WS_COMMAND," in text
    assert '"ha core restart"' in text
    assert '"Another job is running for job group home_assistant_core"' in text
    assert "docker restart homeassistant" not in text
    assert "kill homeassistant" not in text


def test_haos_driver_installs_candidate_into_real_core_config_and_retains_data() -> (
    None
):
    text = DRIVER.read_text(encoding="utf-8")

    assert '.Destination \\"/config\\"' in text
    assert "custom_components/{DOMAIN}" in text
    assert "HA OS retained reference" in text
    assert "KNOWLEDGE_MARKER" in text
    assert "source_id" in text
    assert "second Supervisor Core restart failed" in text


def test_haos_driver_exercises_function_tool_and_supervisor_backup_manager() -> None:
    text = DRIVER.read_text(encoding="utf-8")

    assert 'TOOL_NAME = "haos_marker"' in text
    assert "TOOL_MARKER" in text
    assert '"prompt": "You are a concise assistant.' in text
    assert "DeterministicProvider.requests" in text
    assert '"/backups/new/full"' in text
    assert 'f"/backups/{backup_slug}/restore/full"' in text
    assert '"MUTATED_AFTER_SUPERVISOR_BACKUP"' in text
    assert "_wait_supervisor_job" in text
    assert '"supervisor_full_backup_restore_reboot": True' in text
