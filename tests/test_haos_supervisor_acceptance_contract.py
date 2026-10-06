"""Contracts for the genuine Home Assistant OS + Supervisor VM lane."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "haos-supervisor-vm-acceptance.yml"
DRIVER = ROOT / "ci" / "haos_supervisor_acceptance.py"


def test_haos_vm_workflow_is_weekly_and_manual_only() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    trigger_block = text.split("permissions:", 1)[0]

    assert 'cron: "17 6 * * 0"' in trigger_block
    assert "workflow_dispatch:" in trigger_block
    assert "pull_request:" not in trigger_block
    assert "push:" not in trigger_block
    assert "release:" not in trigger_block


def test_haos_vm_workflow_boots_official_qcow2_with_supervisor_appliance_boundaries() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "home-assistant/operating-system/releases/latest" in text
    assert "generic-x86-64" in text
    assert ".qcow2.xz" in text
    assert "qemu-system-x86_64" in text
    assert "OVMF_CODE" in text
    assert "config.img" in text
    assert "authorized_keys" in text
    assert "usb-storage" in text
    assert "hostfwd=tcp:127.0.0.1:8123-:8123" in text
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
    assert '"/core/restart"' in text
    assert "docker restart homeassistant" not in text
    assert "kill homeassistant" not in text


def test_haos_driver_installs_candidate_into_real_core_config_and_retains_data() -> None:
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
    assert "DeterministicProvider.requests" in text
    assert '"/backups/reload"' in text
    assert '"/backups/info"' in text
    assert '"backup_inventory_reload": True' in text
