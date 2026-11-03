"""Production importer and handler connected across a failing Usage transaction."""

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


@pytest.mark.parametrize(
    "scenario", ["retry", "refresh-retry", "replace-title", "healthy-replace-title"]
)
@pytest.mark.parametrize("detail", ["request", "run"])
async def test_import_retry_after_real_pruning_failure(
    hass, management_agent, management_message, monkeypatch, scenario, detail
):
    entry, subentry = management_agent
    entry.state = ConfigEntryState.NOT_LOADED
    field = f"usage_{detail}_retention_days"
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
    document["title"] = "Imported policy"
    original = manager._detail_storage.async_save
    attempts = []

    async def save(data):
        attempts.append(True)
        if len(attempts) == 1 and scenario != "healthy-replace-title":
            raise OSError("one transient detail-store failure")
        await original(data)

    monkeypatch.setattr(manager._detail_storage, "async_save", save)
    process = await asyncio.create_subprocess_exec(
        "node",
        str(Path(__file__).with_name("retention-import-recovery-wire.mjs")),
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
            "document": json.dumps(document),
            "force_refresh": scenario == "refresh-retry",
            "replacement": "replace-title" in scenario,
            "field": field,
            "agent": {
                "entry_id": entry.entry_id,
                "subentry_id": subentry.subentry_id,
                "title": subentry.title,
            },
        }
    )
    errors = []
    requests = []
    done = None
    while line := await asyncio.wait_for(process.stdout.readline(), 15):
        packet = json.loads(line)
        if "done" in packet:
            done = packet["done"]
            break
        request = packet["request"]
        requests.append(request)
        try:
            if request.get("action") == "agents" and "section" not in request:
                # Catalogue rendering is unrelated scaffolding; import, reads,
                # revision checks and retention all use the real command handler.
                result = {
                    "is_admin": True,
                    "agents": [
                        {
                            "entry_id": entry.entry_id,
                            "subentry_id": subentry.subentry_id,
                            "title": subentry.title,
                        }
                    ],
                    "scopes": [],
                }
            else:
                result = await ui.async_management_command(hass, "admin", True, request)
            await write({"result": result})
        except Exception as error:
            response = {"code": mutation_error_code(error), "message": str(error)}
            errors.append(response)
            await write({"error": response})
    await asyncio.wait_for(process.wait(), 15)
    assert process.returncode == 0, (await process.stderr.read()).decode()
    witness = {
        "scenario": scenario,
        "field": field,
        "snapshots": done,
        "requests": [
            (r.get("section"), r["action"], r.get("revision"), r.get("config"))
            for r in requests
        ],
        "errors": errors,
        "detail_writes": len(attempts),
        "saved_policy": subentry.data[field],
        "stored_details": len(manager._detail_storage.data[f"{detail}s"]),
    }
    print(json.dumps(witness, indent=2))
    if scenario != "healthy-replace-title":
        assert errors[0]["code"] == "eoai_mutation_committed"
    else:
        assert errors == []
    assert len(attempts) == (1 if scenario == "healthy-replace-title" else 2), (
        "UI retry must reach the healthy retention transaction"
    )
    assert manager._detail_storage.data[f"{detail}s"] == []
