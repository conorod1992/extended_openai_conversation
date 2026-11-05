"""Compare explicit same-policy retries through all supported config writes."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import (
    management_ui as ui,
    usage,
)
from custom_components.extended_openai_conversation_responses.mutation_outcomes import (
    mutation_error_code,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import HomeAssistantError
from tests.test_usage_detail_lifecycle import DetailStorage, TotalsStorage


@pytest.mark.parametrize("action", ["save", "update", "import"])
@pytest.mark.parametrize("detail", ["request", "run"])
async def test_same_policy_retry_reaches_deletion(
    hass, management_agent, management_message, monkeypatch, action, detail
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
    baseline = await ui.async_management_command(
        hass, "admin", True, management_message("configuration", "get")
    )
    exported = await ui.async_management_command(
        hass, "admin", True, management_message("configuration", "export")
    )
    document = exported["document"]
    document["config"][field] = 0
    original_save = manager._detail_storage.async_save
    attempts = []

    async def save(data):
        attempts.append(True)
        if len(attempts) == 1:
            raise OSError("one transient store write failure")
        await original_save(data)

    monkeypatch.setattr(manager._detail_storage, "async_save", save)
    payload = (
        {"document": json.dumps(document), "mode": "current", "confirm": True}
        if action == "import"
        else {"config": {field: 0}}
    )
    with pytest.raises(HomeAssistantError) as failure:
        await ui.async_management_command(
            hass,
            "admin",
            True,
            management_message(
                "configuration", action, revision=baseline["revision"], **payload
            ),
        )
    assert mutation_error_code(failure.value) == "eoai_mutation_committed"
    assert subentry.data[field] == 0
    current = await ui.async_management_command(
        hass, "admin", True, management_message("configuration", "get")
    )
    if action == "import":
        preview = await ui.async_management_command(
            hass,
            "admin",
            True,
            management_message(
                "configuration",
                "import_preview",
                document=json.dumps(document),
                mode="current",
            ),
        )
        assert preview["valid"] is True
    result = await ui.async_management_command(
        hass,
        "admin",
        True,
        management_message(
            "configuration", action, revision=current["revision"], **payload
        ),
    )
    witness = {
        "action": action,
        "result": result.get("status", result.get("valid")),
        "write_attempts": len(attempts),
        "saved_retention": subentry.data[field],
        "stored_requests": len(manager._detail_storage.data[collection]),
    }
    print(json.dumps(witness))
    assert (
        (result.get("status") == "updated") if action == "import" else result["valid"]
    )
    assert len(attempts) == 2
    assert manager._detail_storage.data[collection] == []


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
