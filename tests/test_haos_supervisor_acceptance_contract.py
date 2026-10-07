"""Contracts for the genuine Home Assistant OS + Supervisor VM lane."""

import importlib.util
from pathlib import Path
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


def _job_tree(job, nested=False):
    jobs = [{"uuid": "parent", "child_jobs": [job]}] if nested else [job]
    return {"result": "ok", "data": {"jobs": jobs}}


@pytest.mark.parametrize("nested", [False, True])
def test_restore_poll_uses_independent_cli_and_waits_for_exact_job(monkeypatch, nested):
    driver = _driver()
    pending = {"uuid": "restore-job", "done": False}
    completed = {"uuid": "restore-job", "done": True, "errors": []}
    ssh = Mock()
    ssh.json.side_effect = [_job_tree(pending, nested), _job_tree(completed, nested)]
    core_api = Mock(side_effect=AssertionError("Core is stopped during restore"))
    monkeypatch.setattr(driver, "_supervisor_api", core_api)
    monkeypatch.setattr(driver.time, "sleep", lambda _: None)
    assert driver._wait_supervisor_job(ssh, "restore-job") == completed
    core_api.assert_not_called()
    assert ssh.json.call_count == 2
    for call in ssh.json.call_args_list:
        assert call.args == ("ha jobs info --no-progress --raw-json",)
        assert call.kwargs == {"timeout": 30}


@pytest.mark.parametrize(
    "failure",
    [
        AssertionError("SSH permission denied"),
        {"result": "error", "message": "Supervisor unavailable"},
        _job_tree({"uuid": "restore-job", "done": True, "errors": ["restore failed"]}),
    ],
)
def test_restore_poll_does_not_hide_cli_or_restore_errors(failure):
    driver = _driver()
    ssh = Mock()
    ssh.json.side_effect = [failure]
    with pytest.raises(AssertionError):
        driver._wait_supervisor_job(ssh, "restore-job")
    assert ssh.json.call_count == 1


def test_restore_poll_rejects_a_missing_job_instead_of_accepting_another_completion():
    driver = _driver()
    ssh = Mock()
    ssh.json.return_value = _job_tree({"uuid": "unrelated-job", "done": True})
    with pytest.raises(AssertionError, match="restore-job was missing"):
        driver._wait_supervisor_job(ssh, "restore-job")


def test_restore_poll_keeps_its_bounded_deadline(monkeypatch):
    driver = _driver()
    ssh = Mock()
    ssh.json.return_value = _job_tree({"uuid": "restore-job", "done": False})
    clock = iter([0, 0, 2])
    monkeypatch.setattr(driver.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(driver.time, "sleep", lambda _: None)
    with pytest.raises(TimeoutError, match="restore-job did not finish"):
        driver._wait_supervisor_job(ssh, "restore-job", timeout=1)
    assert ssh.json.call_count == 1


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
