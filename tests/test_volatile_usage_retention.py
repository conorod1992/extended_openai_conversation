"""A recovered durable store cannot leave session-local Usage on an old policy."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import (
    management_ui as ui,
    usage,
)
from homeassistant.config_entries import ConfigEntryState


@pytest.mark.parametrize("days", [0, 30])
@pytest.mark.parametrize("action", ["save", "update", "import"])
async def test_committed_policy_reaches_existing_volatile_accounting(
    hass, management_agent, management_message, monkeypatch, days, action
):
    entry, subentry = management_agent
    entry.state = ConfigEntryState.NOT_LOADED
    hass.config_entries.async_update_subentry(
        entry,
        subentry,
        data={
            **subentry.data,
            "usage_request_retention_days": 30 if days == 0 else 0,
            "usage_run_retention_days": 30 if days == 0 else 0,
        },
    )
    native_load = usage._UsageStore.async_load

    async def fail_load(_store):
        raise OSError("transient native store failure")

    monkeypatch.setattr(usage._UsageStore, "async_load", fail_load)
    fallback = await usage.async_get_usage(hass, entry.entry_id, subentry.subentry_id)
    monkeypatch.setattr(usage._UsageStore, "async_load", native_load)
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

    async def record():
        manager = await usage.async_get_usage(
            hass, entry.entry_id, subentry.subentry_id
        )
        assert manager is fallback
        async with manager.async_run():
            await manager.async_record_request(
                successful=True, usage=usage.RequestUsage(total_tokens=5)
            )
        if manager._prune_task:
            await manager._prune_task

    await record()
    # In-memory pruning must not try to turn the fallback into durable storage.
    volatile_native_save = fallback._detail_storage.async_save
    volatile_save = AsyncMock(side_effect=AssertionError("volatile detail save"))
    monkeypatch.setattr(fallback._detail_storage, "async_save", volatile_save)
    baseline = await ui.async_management_command(
        hass, "admin", True, management_message("configuration", "get")
    )
    policy = {"usage_request_retention_days": days, "usage_run_retention_days": days}
    if action == "import":
        artifact = (
            await ui.async_management_command(
                hass, "admin", True, management_message("configuration", "export")
            )
        )["document"]
        artifact["config"].update(policy)
        payload = {"document": artifact, "mode": "current", "confirm": True}
    else:
        payload = {"config": policy}
    await ui.async_management_command(
        hass,
        "admin",
        True,
        management_message(
            "configuration", action, revision=baseline["revision"], **payload
        ),
    )
    assert fallback.request_retention_days == fallback.run_retention_days == days
    assert fallback.requests == [] and fallback.runs == []
    volatile_save.assert_not_awaited()
    durable = await usage.async_get_durable_usage(
        hass, entry.entry_id, subentry.subentry_id
    )
    assert durable is not fallback
    assert durable.request_retention_days == durable.run_retention_days == days
    monkeypatch.setattr(fallback._detail_storage, "async_save", volatile_native_save)
    await record()
    assert len(fallback.requests) == len(fallback.runs) == (0 if days == 0 else 1)
    assert fallback.totals.total_tokens == 10


async def test_failed_durable_prune_still_updates_volatile_privacy_policy(
    hass, management_agent, management_message, monkeypatch
):
    from custom_components.extended_openai_conversation_responses.mutation_outcomes import (
        mutation_error_code,
    )
    from homeassistant.exceptions import HomeAssistantError
    from tests.test_usage_detail_lifecycle import DetailStorage, TotalsStorage

    entry, subentry = management_agent
    entry.state = ConfigEntryState.NOT_LOADED
    fallback = usage.UsageManager(
        usage._VolatileUsageStorage(), detail_storage=usage._VolatileUsageStorage()
    )
    durable = usage.UsageManager(TotalsStorage(), detail_storage=DetailStorage())
    for manager in (fallback, durable):
        await manager.async_initialize()
        async with manager.async_run():
            await manager.async_record_request(
                successful=True, usage=usage.RequestUsage(total_tokens=5)
            )
        if manager._prune_task:
            await manager._prune_task
    key = (entry.entry_id, subentry.subentry_id)
    hass.data[usage._USAGE_MANAGERS] = {key: durable}
    hass.data[usage._VOLATILE_USAGE_MANAGERS] = {key: fallback}
    monkeypatch.setattr(usage, "_register_usage_maintenance", lambda *_: None)
    monkeypatch.setattr(
        ui,
        "async_get_request_rules",
        AsyncMock(return_value=SimpleNamespace(snapshot=lambda: {"rules": []})),
    )
    baseline = await ui.async_management_command(
        hass, "admin", True, management_message("configuration", "get")
    )
    prune = durable.async_prune_details
    monkeypatch.setattr(
        durable,
        "async_prune_details",
        AsyncMock(side_effect=OSError("durable prune unavailable")),
    )
    payload = {"usage_request_retention_days": 0, "usage_run_retention_days": 0}
    with pytest.raises(HomeAssistantError) as error:
        await ui.async_management_command(
            hass,
            "admin",
            True,
            management_message(
                "configuration", "save", config=payload, revision=baseline["revision"]
            ),
        )
    assert mutation_error_code(error.value) == "eoai_mutation_committed"
    assert fallback.request_retention_days == fallback.run_retention_days == 0
    assert fallback.requests == [] and fallback.runs == []
    assert durable.requests and durable.runs
    monkeypatch.setattr(durable, "async_prune_details", prune)
    baseline = await ui.async_management_command(
        hass, "admin", True, management_message("configuration", "get")
    )
    await ui.async_management_command(
        hass,
        "admin",
        True,
        management_message(
            "configuration", "save", config=payload, revision=baseline["revision"]
        ),
    )
    assert durable.requests == [] and durable.runs == []
