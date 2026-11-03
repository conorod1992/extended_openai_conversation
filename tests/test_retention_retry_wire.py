"""Retention deletion retries across the real frontend and backend save stages."""

import asyncio
import json
from pathlib import Path
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
from tests.test_usage_detail_lifecycle import DetailStorage, TotalsStorage


@pytest.mark.parametrize("fail_first", [False, True])
async def test_retention_failure_retry_through_real_panel(
    hass, management_agent, management_message, monkeypatch, fail_first
):
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
        AsyncMock(return_value=SimpleNamespace(snapshot=lambda: {"rules": []})),
    )
    baseline = await ui.async_management_command(
        hass, "admin", True, management_message("configuration", "get")
    )
    actual_prune = manager.async_prune_details
    prune_calls = []
    actual_save = manager._detail_storage.async_save
    writes = []

    async def fail_first_write(value):
        writes.append(True)
        if fail_first and len(writes) == 1:
            raise OSError("transient detail-store failure")
        await actual_save(value)

    monkeypatch.setattr(manager._detail_storage, "async_save", fail_first_write)

    async def track_prune():
        prune_calls.append(True)
        return await actual_prune()

    monkeypatch.setattr(manager, "async_prune_details", track_prune)
    runner = Path(__file__).with_name("retention-retry-wire.mjs")
    process = await asyncio.create_subprocess_exec(
        "node",
        str(runner),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    async def write(value):
        process.stdin.write((json.dumps(value) + "\n").encode())
        await process.stdin.drain()

    await write(
        {
            "baseline": baseline,
            "agent": {
                "entry_id": entry.entry_id,
                "subentry_id": subentry.subentry_id,
                "title": subentry.title,
            },
        }
    )
    requests = []
    errors = []
    done = None
    while line := await asyncio.wait_for(process.stdout.readline(), 15):
        packet = json.loads(line)
        if "done" in packet:
            done = packet["done"]
            break
        request = packet["request"]
        requests.append(request)
        try:
            result = await ui.async_management_command(hass, "admin", True, request)
            await write({"result": result})
        except Exception as error:
            response = {"code": mutation_error_code(error), "message": str(error)}
            errors.append(response)
            await write({"error": response})
    await asyncio.wait_for(process.wait(), 15)
    assert process.returncode == 0, (await process.stderr.read()).decode()
    witness = {
        "fail_first": fail_first,
        "snapshots": done,
        "requests": [
            (item["section"], item["action"], item.get("revision")) for item in requests
        ],
        "errors": errors,
        "prune_calls": len(prune_calls),
        "persisted_title": subentry.title,
        "persisted_retention": subentry.data["usage_request_retention_days"],
        "retained_detail_store_requests": len(manager._detail_storage.data["requests"]),
    }
    print(json.dumps(witness, indent=2))
    assert done is not None
    if fail_first:
        assert errors[0]["code"] == "eoai_mutation_committed"
        assert len(errors) == 1
        assert done[0]["dirty"] is True
        assert done[0]["revision"] != baseline["revision"]
    assert len(prune_calls) >= (2 if fail_first else 1), (
        "An ordinary retry after the storage recovers must reach pruning again"
    )
    assert subentry.title == "Unrelated title edit"
    assert len(manager._detail_storage.data["requests"]) == 0
