"""Corrupt restore journals stay quarantined across genuine HA cold boots."""

from pathlib import Path

import pytest

from tests_real_ha.test_restore_transaction_process_crash import (
    DOMAIN,
    _configuration,
    _kill_at,
    _run,
    _stage_component,
)
from tests_stress.conftest import record


@pytest.mark.usefixtures("socket_enabled")
def test_corrupt_restore_journal_cold_boot_isolation_and_repair(tmp_path, stress_trace):
    source = Path(__file__).resolve().parents[1] / "custom_components" / DOMAIN
    config_dir = tmp_path / "ha-config"
    destination = config_dir / "custom_components" / DOMAIN
    destination.parent.mkdir(parents=True)
    _stage_component(source, destination)
    (config_dir / "configuration.yaml").write_text(
        _configuration("Restore Recovery Isolation")
    )
    _run(config_dir, "seed", "partial")
    _kill_at(config_dir, "restore", "partial", "restore-paused")
    journals = list((config_dir / ".storage").glob(f"{DOMAIN}.restore_transaction.*"))
    assert len(journals) == 1
    journal = journals[0]
    authoritative = journal.read_bytes()
    damaged = authoritative[: len(authoritative) // 2]
    journal.write_bytes(damaged)
    _run(config_dir, "quarantine", "partial")
    assert journal.read_bytes() == damaged
    assert not list(journal.parent.glob(journal.name + ".corrupt*"))
    journal.write_bytes(authoritative)
    _run(config_dir, "verify", "partial")
    assert not journal.exists()
    _run(config_dir, "acknowledge", "partial")
    _run(config_dir, "verify-acknowledged", "partial")
    record(
        stress_trace,
        "summary",
        restore_recovery_cold_cases=1,
        process_fresh_boots=5,
        ha_service_calls=1,
        rejected_recovery_operations=2,
    )
