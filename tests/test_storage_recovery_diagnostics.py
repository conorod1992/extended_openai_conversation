"""Recovery observations preserve evidence and do not initialize rejected stores."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from custom_components.extended_openai_conversation_responses import diagnostics
from custom_components.extended_openai_conversation_responses.agent_maintenance import (
    get_agent_maintenance_gate,
)
from custom_components.extended_openai_conversation_responses.storage_recovery_diagnostics import (
    storage_recovery_snapshot,
)
from tests.test_diagnostics import (
    _conversation_subentry,
    _entry,
    _patch_healthy_collectors,
)

PREFIX = "extended_openai_conversation_responses"


def _file(directory, suffix, content="private retained source"):
    path = directory / f"{PREFIX}.{suffix}"
    path.write_text(content, encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("name", "category"),
    [
        ("memory", "persistent_memory"),
        ("temporary_memory", "temporary_memory"),
        ("knowledge", "knowledge"),
        ("guest_mode", "guest_mode"),
        ("request_rules", "request_rules"),
        ("archive", "archive"),
        ("usage", "usage"),
    ],
)
def test_snapshot_reads_no_contents_and_distinguishes_replaced_evidence(
    tmp_path, name, category
):
    suffix = ".metadata" if name == "archive" else ""
    base = f"{name}.entry.agent{suffix}"
    evidence = _file(tmp_path, base + ".corrupt.private-date")
    _file(tmp_path, f"{name}.entry.agent-other{suffix}.corrupt.neighbor")
    _file(tmp_path, f"{name}.entry-other.agent{suffix}.corrupt.neighbor")
    _file(tmp_path, "memory.entry.agent.embeddings.corrupt.cache")
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    with patch.object(Path, "open", side_effect=AssertionError("must not open data")):
        result = storage_recovery_snapshot(str(tmp_path), "entry", "agent")
    assert result["stores"][category] == {
        "active_files": 0,
        "preserved_corrupt_files": 1,
        "unavailable_stores": 1,
    }
    assert result["restore_journal"]["state"] == "absent"
    assert "clean assistant" in result["guidance"]
    assert result["content_validated"] is False
    assert "private" not in json.dumps(result)
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before

    # A replacement can coexist with historical evidence. Existence is not a
    # claim about the new file's JSON validity, and never authorizes deletion.
    active = _file(tmp_path, base, "{not valid JSON")
    result = storage_recovery_snapshot(str(tmp_path), "entry", "agent")
    assert result["stores"][category] == {
        "active_files": 1,
        "preserved_corrupt_files": 1,
        "unavailable_stores": 0,
    }
    assert "guidance" not in result
    assert active.read_text() == "{not valid JSON"
    assert evidence.read_bytes() == before[evidence.name]


def test_partition_usage_and_journal_observations_are_exact_and_content_free(tmp_path):
    _file(tmp_path, "archive.entry.agent.turns.2026-09.corrupt.one")
    _file(tmp_path, "archive.entry.agent.turns.2026-09.corrupt.two")
    _file(tmp_path, "archive.entry.agent.turns.2026-10")
    _file(tmp_path, "archive.entry.agent.turns.2026-13.corrupt.invalid-month")
    _file(tmp_path, "archive.entry.agent.turns.2026-10.extra.corrupt.unowned")
    _file(tmp_path, "usage.entry.agent")
    _file(tmp_path, "usage.entry.agent.daily.corrupt.one")
    _file(tmp_path, "usage.entry.agent.details")
    journal = _file(tmp_path, "restore_transaction.entry.agent", "{damaged journal")
    report = storage_recovery_snapshot(str(tmp_path), "entry", "agent")
    assert report["stores"]["archive"] == {
        "active_files": 1, "preserved_corrupt_files": 2, "unavailable_stores": 1,
    }
    assert report["stores"]["usage"] == {
        "active_files": 2, "preserved_corrupt_files": 1, "unavailable_stores": 1,
    }
    assert report["restore_journal"]["state"] == "present"
    assert report["content_validated"] is False
    assert journal.read_text() == "{damaged journal"
    assert "damaged journal" not in json.dumps(report)


def test_missing_directory_and_non_file_store_do_not_look_healthy(tmp_path):
    report = storage_recovery_snapshot(str(tmp_path / "missing"), "entry", "agent")
    assert all(not any(value.values()) for value in report["stores"].values())
    (tmp_path / f"{PREFIX}.memory.entry.agent").mkdir()
    report = storage_recovery_snapshot(str(tmp_path), "entry", "agent")
    assert report["stores"]["persistent_memory"]["unavailable_stores"] == 1


def test_active_file_symlink_is_not_mistaken_for_missing_quarantined_data(tmp_path):
    target = tmp_path / "private-target"
    target.write_text("{not validated", encoding="utf-8")
    active = tmp_path / f"{PREFIX}.memory.entry.agent"
    try:
        active.symlink_to(target)
    except OSError:
        pytest.skip("Creating file symlinks is unavailable on this host")
    _file(tmp_path, "memory.entry.agent.corrupt.original")
    with patch.object(Path, "open", side_effect=AssertionError("must not open data")):
        report = storage_recovery_snapshot(str(tmp_path), "entry", "agent")
    assert report["stores"]["persistent_memory"] == {
        "active_files": 1, "preserved_corrupt_files": 1, "unavailable_stores": 0,
    }
    assert report["content_validated"] is False


async def test_diagnostics_skips_quarantined_getters_but_keeps_healthy_sections(
    hass, monkeypatch
):
    collectors = _patch_healthy_collectors(monkeypatch)
    directory = Path(hass.config.path(".storage"))
    directory.mkdir(parents=True, exist_ok=True)
    _file(directory, "memory.entry.agent.corrupt.private-name")
    _file(directory, "archive.entry.agent.turns.2026-10.corrupt.private-name")
    _file(directory, "usage.entry.agent.daily.corrupt.private-name")
    before = {path.name: path.read_bytes() for path in directory.iterdir()}
    result = await diagnostics.async_get_config_entry_diagnostics(
        hass, _entry(_conversation_subentry())
    )
    agent = result["conversation_agents"][0]
    for collector in (collectors.memory, collectors.archive, collectors.usage):
        collector.assert_not_awaited()
    collectors.temporary.assert_awaited_once()
    collectors.knowledge.assert_awaited_once()
    collectors.guest.assert_awaited_once()
    assert agent["storage_error"] == "StorageRepairRequired"
    assert agent["archive_storage_error"] == "StorageRepairRequired"
    assert agent["usage_storage_error"] == "StorageRepairRequired"
    assert agent["knowledge_items"] == 3
    assert agent["storage_recovery"]["restore_journal"]["state"] == "absent"
    assert "private-name" not in json.dumps(result)
    assert {path.name: path.read_bytes() for path in directory.iterdir()} == before


async def test_pending_recovery_diagnostics_do_not_enter_any_retained_getter(
    hass, monkeypatch
):
    collectors = _patch_healthy_collectors(monkeypatch)
    gate = get_agent_maintenance_gate(hass, "entry", "agent")
    gate.recovery_required = True
    result = await diagnostics.async_get_config_entry_diagnostics(
        hass, _entry(_conversation_subentry())
    )
    for collector in vars(collectors).values():
        collector.assert_not_awaited()
    assert result["conversation_agents"][0]["restore_recovery_pending"] is True
    assert gate.recovery_required


async def test_unavailable_directory_reports_sanitized_error_and_keeps_other_facts(
    hass, monkeypatch
):
    _patch_healthy_collectors(monkeypatch)

    def denied(*_):
        raise PermissionError("private/path must not escape")

    monkeypatch.setattr(diagnostics, "storage_recovery_snapshot", denied)
    result = await diagnostics.async_get_config_entry_diagnostics(
        hass, _entry(_conversation_subentry())
    )
    agent = result["conversation_agents"][0]
    assert agent["storage_recovery_error"] == "PermissionError"
    assert agent["memory_items"] == 2 and agent["knowledge_items"] == 3
    assert "private/path" not in json.dumps(result)
