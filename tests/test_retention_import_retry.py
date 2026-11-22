"""Compare explicit same-policy retries through all supported config writes."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import (
    management_loading_performance as loading,
    management_ui as ui,
    usage,
)
from custom_components.extended_openai_conversation_responses.mutation_outcomes import (
    mutation_error_code,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import HomeAssistantError
from tests.test_function_repair_configuration_contract import _repairable_agent
from tests.test_usage_detail_lifecycle import DetailStorage, TotalsStorage


async def test_configuration_overwrite_preserves_archive_retention(
    hass, management_agent, management_message, monkeypatch
):
    entry, subentry = management_agent
    entry.state = ConfigEntryState.NOT_LOADED
    field = "archive_retention_days"
    subentry.data = {**subentry.data, field: 365}
    monkeypatch.setattr(
        ui,
        "async_get_request_rules",
        AsyncMock(
            return_value=SimpleNamespace(
                revision=lambda: "rules-0",
                async_backup_data=AsyncMock(return_value={"rules": []}),
            )
        ),
    )
    baseline = await ui.async_management_command(
        hass, "admin", True, management_message("configuration", "get")
    )
    exported = await ui.async_management_command(
        hass, "admin", True, management_message("configuration", "export")
    )
    document = exported["document"]
    document["config"].pop(field, None)
    payload = {"document": json.dumps(document), "mode": "current"}
    preview = await ui.async_management_command(
        hass, "admin", True,
        management_message("configuration", "import_preview", **payload),
    )
    assert preview["config"][field] == 365
    result = await ui.async_management_command(
        hass, "admin", True,
        management_message("configuration", "import", confirm=True,
                           revision=baseline["revision"], **payload),
    )
    assert result["status"] == "updated"
    assert subentry.data[field] == 365


@pytest.mark.parametrize(
    ("action", "module", "method"),
    [
        ("import", loading, "_snapshot_normalized_configuration"),
        ("import", ui, "seed_persisted_config_projection"),
        ("save", ui, "seed_persisted_config_projection"),
        ("update", ui, "seed_persisted_config_projection"),
    ],
    ids=["import-snapshot", "import-cache", "save-cache", "update-cache"],
)
@pytest.mark.parametrize("prune_fails", [False, True])
async def test_configuration_write_enforces_retention_after_optional_cache_failure(
    hass,
    management_agent,
    management_message,
    monkeypatch,
    action,
    module,
    method,
    prune_fails,
):
    entry, subentry = management_agent
    entry.state = ConfigEntryState.NOT_LOADED
    revision = ui.saved_agent_config_revision(subentry, subentry.data, subentry.title)
    field = "usage_request_retention_days"
    if action == "import":
        monkeypatch.setattr(
            ui,
            "async_get_request_rules",
            AsyncMock(
                return_value=SimpleNamespace(
                    revision=lambda: "rules-0",
                    async_backup_data=AsyncMock(return_value={"rules": []}),
                )
            ),
        )
        exported = await ui.async_management_command(
            hass, "admin", True, management_message("configuration", "export")
        )
        document = exported["document"]
        document["config"][field] = 0
        payload = {
            "document": json.dumps(document),
            "mode": "current",
            "confirm": True,
            "confirm_retention": True,
        }
    else:
        payload = {"config": {field: 0}}

    cache_observed_policy = []

    def fail_cache(*_args, **_kwargs):
        cache_observed_policy.append(subentry.data.get(field))
        raise RuntimeError("optional cache failure")

    async def prune(*_args):
        assert subentry.data[field] == 0
        if prune_fails:
            raise OSError("required retention write failed")

    prune_usage = AsyncMock(side_effect=prune)
    monkeypatch.setattr(module, method, fail_cache)
    monkeypatch.setattr(usage, "async_apply_usage_retention", prune_usage)
    request = management_message(
        "configuration", action, revision=revision, **payload
    )
    if prune_fails:
        with pytest.raises(HomeAssistantError, match="Configuration saved") as failure:
            await ui.async_management_command(hass, "admin", True, request)
        assert mutation_error_code(failure.value) == "eoai_mutation_committed"
    else:
        result = await ui.async_management_command(hass, "admin", True, request)
        if action == "import":
            assert result["status"] == "updated"
            assert result["subentry_id"] == subentry.subentry_id
        else:
            assert result["valid"] is True
            assert result["config"][field] == 0
        assert result["revision"] == ui.saved_agent_config_revision(
            subentry, subentry.data, subentry.title
        )
        assert result["revision"] != revision
    assert cache_observed_policy == [0]
    prune_usage.assert_awaited_once_with(hass, entry.entry_id, subentry.subentry_id)


@pytest.mark.parametrize("action", ["save", "update"])
async def test_configuration_write_rechecks_revision_before_pruning(
    hass, management_agent, management_message, monkeypatch, action
):
    entry, subentry = management_agent
    entry.state = ConfigEntryState.NOT_LOADED
    revision = ui.saved_agent_config_revision(subentry, subentry.data, subentry.title)
    field = "usage_request_retention_days"
    newer_data = {**subentry.data, "prompt": "Saved by a newer edit"}

    async def validate(*_args):
        subentry.data = newer_data

    monkeypatch.setattr(ui, "_async_validate_configuration_request", validate)
    prune_usage = AsyncMock()
    monkeypatch.setattr(usage, "async_apply_usage_retention", prune_usage)
    request = management_message(
        "configuration", action, revision=revision, config={field: 0}
    )
    with pytest.raises(HomeAssistantError, match="changed"):
        await ui.async_management_command(hass, "admin", True, request)

    assert subentry.data == newer_data
    prune_usage.assert_not_awaited()


@pytest.mark.parametrize(
    ("section", "action"),
    [
        ("configuration", "save"),
        ("configuration", "update"),
        ("configuration", "import"),
        ("settings", "update"),
        ("function_repair", "configuration_save"),
    ],
)
@pytest.mark.parametrize("detail", ["request", "run"])
async def test_same_policy_retry_reaches_deletion(
    hass, management_agent, management_message, monkeypatch, section, action, detail
):
    field = f"usage_{detail}_retention_days"
    collection = f"{detail}s"
    entry, subentry = management_agent
    entry.state = ConfigEntryState.NOT_LOADED
    manager = usage.UsageManager(TotalsStorage(), detail_storage=DetailStorage())
    await manager.async_initialize()
    async with manager.async_run():
        await manager.async_record_request(
            successful=True, usage=usage.RequestUsage(total_tokens=5)
        )
    if manager._prune_task:
        await manager._prune_task
    hass.data[usage._USAGE_MANAGERS] = {(entry.entry_id, subentry.subentry_id): manager}
    monkeypatch.setattr(usage, "_register_usage_maintenance", lambda *_: None)
    monkeypatch.setattr(
        ui,
        "async_get_request_rules",
        AsyncMock(
            return_value=SimpleNamespace(
                snapshot=lambda: {"rules": []},
                revision=lambda: "rules-0",
                async_backup_data=AsyncMock(return_value={"rules": []}),
            )
        ),
    )
    revision_section, revision_action = "configuration", "get"
    if section == "function_repair":
        _, repairable = _repairable_agent()
        subentry.data = {**subentry.data, **repairable.data}
        revision_section, revision_action = "function_repair", "configuration_get"
    baseline = await ui.async_management_command(
        hass, "admin", True, management_message(revision_section, revision_action)
    )
    if action == "import":
        exported = await ui.async_management_command(
            hass, "admin", True, management_message("configuration", "export")
        )
        document = exported["document"]
        document["config"][field] = 0
        payload = {
            "document": json.dumps(document),
            "mode": "current",
            "confirm": True,
            "confirm_retention": True,
        }
    else:
        field_owner = "settings" if section == "settings" else "config"
        payload = {field_owner: {field: 0}}
    original_save = manager._detail_storage.async_save
    attempts = []

    async def save(data):
        attempts.append(True)
        if len(attempts) == 1:
            raise OSError("one transient store write failure")
        await original_save(data)

    monkeypatch.setattr(manager._detail_storage, "async_save", save)
    with pytest.raises(HomeAssistantError) as failure:
        await ui.async_management_command(
            hass,
            "admin",
            True,
            management_message(
                section, action, revision=baseline["revision"], **payload
            ),
        )
    assert mutation_error_code(failure.value) == "eoai_mutation_committed"
    assert subentry.data[field] == 0
    current = await ui.async_management_command(
        hass, "admin", True, management_message(revision_section, revision_action)
    )
    if action == "import":
        preview = await ui.async_management_command(
            hass,
            "admin",
            True,
            management_message(
                "configuration",
                "import_preview",
                document=payload["document"],
                mode="current",
            ),
        )
        assert preview["valid"] is True
    result = await ui.async_management_command(
        hass,
        "admin",
        True,
        management_message(
            section, action, revision=current["revision"], **payload
        ),
    )
    witness = {
        "action": action,
        "section": section,
        "result": result.get("status", result.get("valid")),
        "write_attempts": len(attempts),
        "saved_retention": subentry.data[field],
        "stored_requests": len(manager._detail_storage.data[collection]),
    }
    print(json.dumps(witness))
    if action == "import":
        assert result["status"] == "updated"
    elif section == "settings":
        assert result["settings"][field] == 0
    else:
        assert result["valid"] is True
    assert len(attempts) == 2
    assert manager._detail_storage.data[collection] == []


async def test_preview_does_not_prune_retention_committed_by_another_command(
    hass, management_agent, management_message, monkeypatch
):
    entry, subentry = management_agent
    entry.state = ConfigEntryState.NOT_LOADED
    entered = asyncio.Event()
    release = asyncio.Event()

    async def preview(*_):
        entered.set()
        await release.wait()
        return {"preview": True}

    monkeypatch.setattr(ui, "_async_preview_effective_request", preview)
    prune = AsyncMock()
    monkeypatch.setattr(usage, "async_apply_usage_retention", prune)
    baseline = await ui.async_management_command(
        hass, "admin", True, management_message("configuration", "get")
    )
    pending = asyncio.create_task(
        ui.async_management_command(
            hass, "admin", True, management_message("configuration", "request_preview")
        )
    )
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        result = await ui.async_management_command(
            hass,
            "admin",
            True,
            management_message(
                "configuration",
                "save",
                revision=baseline["revision"],
                config={"usage_request_retention_days": 0},
            ),
        )
        assert result["valid"] is True
    finally:
        release.set()
        preview_result = await asyncio.wait_for(pending, timeout=2)

    assert preview_result == {"preview": True}
    prune.assert_awaited_once_with(hass, entry.entry_id, subentry.subentry_id)


async def test_import_into_new_assistant_does_not_prune_selected_source(
    hass, management_agent, management_message, monkeypatch
):
    entry, subentry = management_agent
    entry.state = ConfigEntryState.NOT_LOADED
    monkeypatch.setattr(
        ui,
        "async_get_request_rules",
        AsyncMock(
            return_value=SimpleNamespace(
                snapshot=lambda: {"rules": []},
                revision=lambda: "rules-0",
                async_backup_data=AsyncMock(return_value={"rules": []}),
            )
        ),
    )
    durable_usage = AsyncMock(side_effect=AssertionError("source must not be pruned"))
    monkeypatch.setattr(usage, "async_get_durable_usage", durable_usage)
    exported = await ui.async_management_command(
        hass, "admin", True, management_message("configuration", "export")
    )
    document = exported["document"]
    document["config"]["usage_request_retention_days"] = 0
    original = dict(subentry.data)
    result = await ui.async_management_command(
        hass,
        "admin",
        True,
        management_message(
            "configuration", "import", document=json.dumps(document), mode="new"
        ),
    )
    assert result["status"] == "created"
    assert result["subentry_id"] != subentry.subentry_id
    assert subentry.data == original
    durable_usage.assert_not_awaited()
