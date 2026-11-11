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
