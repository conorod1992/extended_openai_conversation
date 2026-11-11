"""Known provider context must survive validation without rewriting raw data."""

from copy import deepcopy

import pytest

from custom_components.extended_openai_conversation_responses import backup, transfer
from custom_components.extended_openai_conversation_responses.agent_config import (
    normalize_agent_config,
)
from homeassistant.exceptions import HomeAssistantError
from tests.test_private_backup_fidelity import private_document


def raw_config():
    return {
        "chat_model": "o3-pro",
        "api_mode": "responses",
        "reasoning_effort": None,
        "functions": "# Keep authored YAML\n[]\n",
        "function_groups": [],
        "future_field": {"keep": True},
    }


def test_openai_helper_null_requires_known_provider():
    raw = raw_config()
    known = {k: v for k, v in raw.items() if k != "future_field"}
    assert (
        normalize_agent_config(known, provider="openai").get("reasoning_effort") is None
    )
    with pytest.raises(HomeAssistantError, match="reasoning_effort"):
        normalize_agent_config(known)
    with pytest.raises(HomeAssistantError, match="reasoning_effort"):
        normalize_agent_config(known, provider="azure")
    original = deepcopy(raw)
    backup.private_configuration_snapshot(raw, provider="openai")
    assert raw == original
    with pytest.raises(HomeAssistantError):
        backup.private_configuration_snapshot(
            {**raw, "max_tokens": -1}, provider="openai"
        )


def test_private_backup_and_full_transfer_inspection_use_source_provider():
    document = private_document()
    raw = raw_config()
    document["agent"]["config"] = deepcopy(raw)
    document["provider_connection"] = {
        "api_provider": "openai",
        "api_key": "test-secret",
    }
    finalized = backup.finalize_backup_snapshot(document)["document"]
    assert backup.inspect_backup(finalized, "agent").config == raw
    assert transfer.inspect_transfer(finalized, "agent").config == raw
    document["provider_connection"]["api_provider"] = "azure"
    with pytest.raises(backup.BackupError, match="reasoning_effort"):
        backup.inspect_backup(
            backup.finalize_backup_snapshot(document)["document"], "agent"
        )


@pytest.mark.parametrize("fail", [False, True])
async def test_selective_restore_and_rollback_preserve_explicit_null(
    hass, monkeypatch, fail
):
    from tests.test_selective_restore_concurrency import RestoreHarness

    tx = RestoreHarness(monkeypatch)
    tx.entry.data = {"api_provider": "openai"}
    tx.subentry.data = raw_config()
    before = deepcopy(tx.subentry.data)
    if fail:
        tx.fail_knowledge_writes = 1
        with pytest.raises(backup.BackupError):
            await tx.restore()
    else:
        await tx.restore()
    assert tx.subentry.data == before


async def test_setup_export_uses_parent_context_without_exporting_credentials(
    hass, monkeypatch
):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from custom_components.extended_openai_conversation_responses.request_rules import (
        RequestRules,
    )
    from tests.test_request_rules import MemoryStore

    rules = RequestRules(MemoryStore())
    await rules.async_initialize()
    monkeypatch.setattr(
        transfer, "async_get_request_rules", AsyncMock(return_value=rules)
    )
    entry = SimpleNamespace(
        entry_id="entry", data={"api_provider": "openai", "api_key": "never-export-me"}
    )
    subentry = SimpleNamespace(subentry_id="agent", title="Agent", data=raw_config())
    before = deepcopy(subentry.data)
    result = await transfer.async_create_setup_export(hass, entry, subentry)
    assert "never-export-me" not in result["json"]
    assert "provider_connection" not in result["document"]
    assert subentry.data == before


@pytest.mark.parametrize(
    "provider,effort", [("openai", None), ("openai", "high"), ("azure", "high")]
)
async def test_private_create_inspect_preserves_provider_and_raw_yaml(
    hass, monkeypatch, provider, effort
):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from tests.test_backup import _document

    source = _document()
    source["guest_mode"] = {"schedule": None}
    raw = raw_config()
    raw["reasoning_effort"] = effort
    entry = SimpleNamespace(
        entry_id="entry", data={"api_provider": provider, "api_key": "private-source"}
    )
    subentry = SimpleNamespace(subentry_id="agent", title="Source", data=raw)
    sections = {
        "memory": "memories",
        "temporary": "temporary_memories",
        "knowledge": "knowledge",
        "archive": "archive",
        "usage": "usage",
        "guest_mode": "guest_mode",
        "request_rules": "request_rules",
    }
    managers = SimpleNamespace(
        **{
            name: SimpleNamespace(
                async_backup_data=AsyncMock(return_value=source[section])
            )
            for name, section in sections.items()
        }
    )
    monkeypatch.setattr(backup, "_managers", AsyncMock(return_value=managers))
    created = await backup.async_create_backup(hass, entry, subentry)
    assert backup.inspect_backup(created["document"], "agent").config == raw
    assert transfer.inspect_transfer(created["document"], "agent").config == raw
    assert subentry.data == raw


def _connection(provider):
    result = {
        "api_provider": provider,
        "api_key": "private-test",
        "skip_authentication": True,
    }
    if provider == "azure":
        result.update(
            base_url="https://example.openai.azure.com",
            api_version="2025-04-01-preview",
        )
    return result


def _provider_config(provider):
    value = raw_config()
    value["reasoning_effort"] = None if provider == "openai" else "high"
    return value


@pytest.mark.parametrize("old,new", [("azure", "openai"), ("openai", "azure")])
async def test_cross_provider_restore_validates_each_generation(
    hass, monkeypatch, old, new
):
    from unittest.mock import MagicMock

    from tests.test_selective_restore_concurrency import RestoreHarness

    tx = RestoreHarness(monkeypatch)
    tx.entry.data = _connection(old)
    tx.subentry.data = _provider_config(old)
    previous = deepcopy(tx.entry.data)
    document = private_document()
    document["agent"]["config"] = _provider_config(new)
    document["provider_connection"] = _connection(new)

    def update_entry(entry, *, data):
        assert tx.journal.data is None
        entry.data = deepcopy(data)

    tx.hass.config_entries.async_update_entry = update_entry
    tx.hass.config_entries.async_schedule_reload = MagicMock()
    result = await transfer.async_restore_transfer(
        tx.hass,
        tx.entry,
        tx.subentry,
        backup.finalize_backup_snapshot(document)["document"],
        sections=[transfer.SECTION_CONFIGURATION],
        provider_connection=document["provider_connection"],
        expected_provider_data=previous,
    )
    assert result["provider_connection_restored"]
    assert tx.entry.data == _connection(new)
    assert tx.subentry.data == _provider_config(new)
    assert tx.journal.saved[-1]["configuration_providers"] == {
        "target": new,
        "rollback": old,
    }
    assert "private-test" not in str(tx.journal.saved[-1]["configuration_providers"])


@pytest.mark.parametrize("old,new", [("azure", "openai"), ("openai", "azure")])
@pytest.mark.parametrize(
    "phase,published", [("applying", False), ("committed", False), ("committed", True)]
)
@pytest.mark.parametrize("parent_changed", [False, True])
async def test_cross_provider_journal_replay_retains_generation_context(
    hass, monkeypatch, old, new, phase, published, parent_changed
):
    from custom_components.extended_openai_conversation_responses import (
        restore_recovery,
    )
    from tests.test_selective_restore_concurrency import RestoreHarness

    tx = RestoreHarness(monkeypatch)
    tx.entry.data = _connection(new if parent_changed else old)
    before = deepcopy(tx.state)
    before.config = _provider_config(old)
    target = deepcopy(before)
    target.config = _provider_config(new)
    target.title = "New provider agent"
    tx.subentry.data = deepcopy(target.config if published else before.config)
    journal = restore_recovery._new_journal(
        tx.entry.entry_id,
        tx.subentry.subentry_id,
        target,
        before,
        target_provider=new,
        rollback_provider=old,
    )
    journal["phase"] = phase
    tx.journal.data = deepcopy(journal)
    assert await restore_recovery.async_recover_pending_restore(
        tx.hass, tx.entry, tx.subentry
    )
    assert tx.subentry.data == (
        target.config if phase == "committed" else before.config
    )
    assert tx.journal.data is None
    # Replay validates configurations; provider credentials remain a separate confirmed update.
    assert tx.entry.data == _connection(new if parent_changed else old)


@pytest.mark.parametrize(
    "contexts",
    [
        {},
        {"target": "openai", "rollback": "unknown"},
        {"target": None, "rollback": "azure"},
        "openai",
    ],
)
async def test_journal_rejects_invalid_provider_context(hass, contexts):
    from custom_components.extended_openai_conversation_responses import (
        restore_recovery,
    )
    from tests.test_restore_recovery import _states

    target, before = _states()
    journal = restore_recovery._new_journal("entry", "agent", target, before)
    journal["configuration_providers"] = contexts
    with pytest.raises(backup.BackupError, match="provider"):
        await restore_recovery._async_load_journal(
            journal, "entry", "agent", provider="openai"
        )


async def test_cross_provider_configuration_without_selected_connection_is_rejected(
    hass, monkeypatch
):
    from tests.test_selective_restore_concurrency import RestoreHarness

    tx = RestoreHarness(monkeypatch)
    tx.entry.data = _connection("azure")
    tx.subentry.data = _provider_config("azure")
    before = deepcopy(tx.subentry.data)
    document = private_document()
    document["agent"]["config"] = _provider_config("openai")
    document["provider_connection"] = _connection("openai")
    with pytest.raises(backup.BackupError):
        await transfer.async_restore_transfer(
            tx.hass,
            tx.entry,
            tx.subentry,
            backup.finalize_backup_snapshot(document)["document"],
            sections=[transfer.SECTION_CONFIGURATION],
        )
    assert tx.subentry.data == before
    assert not tx.journal.saved and not tx.writes


async def test_legacy_journal_does_not_invent_missing_openai_context(hass):
    from custom_components.extended_openai_conversation_responses import (
        restore_recovery,
    )
    from tests.test_restore_recovery import _states

    target, before = _states()
    target.config = _provider_config("openai")
    before.config = _provider_config("azure")
    journal = restore_recovery._new_journal("entry", "agent", target, before)
    journal["phase"] = "committed"
    with pytest.raises(backup.BackupError, match="corrupted"):
        await restore_recovery._async_load_journal(
            journal, "entry", "agent", provider="azure"
        )
