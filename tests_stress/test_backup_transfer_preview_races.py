"""Real WebSocket transfer previews must bind Apply to the latest target state."""

from __future__ import annotations

from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockUser

from custom_components.extended_openai_conversation_responses import backup_transfer
from custom_components.extended_openai_conversation_responses.memory import (
    async_get_memory,
)
from homeassistant.core import HomeAssistant
from tests_real_ha.test_backup_transfer_protocol import (
    _conversation_subentry,
    _download_archive,
    _entry,
    _setup_entry,
    _transfer_call,
    _upload_archive,
    _user_token,
)
from tests_stress.conftest import record


@pytest.mark.asyncio
async def test_two_imports_reject_stale_preview_and_changed_target(
    hass: HomeAssistant,
    hass_ws_client: Any,
    stress_trace: list[dict],
) -> None:
    entry = _entry()
    await _setup_entry(hass, entry)
    subentry = _conversation_subentry(entry)
    admin = MockUser(id="transfer-race-admin", name="Transfer race", is_owner=True)
    client = await hass_ws_client(hass, await _user_token(hass, admin))
    memory = await async_get_memory(hass, entry.entry_id, subentry.subentry_id)
    owner = "transfer-race-owner"
    await memory.async_add(owner, "BACKUP-A", "acceptance", "explicit")
    archive_a, metadata_a = await _download_archive(client, entry=entry, mode="full")
    await memory.async_add(owner, "BACKUP-B", "acceptance", "explicit")
    archive_b, metadata_b = await _download_archive(client, entry=entry, mode="full")

    # An abandoned upload must release capacity immediately for a retry.
    cancelled = await _transfer_call(
        client,
        entry=entry,
        action="import_start",
        data={"filename": metadata_a["filename"], "size": len(archive_a)},
    )
    assert cancelled["success"], cancelled
    cancelled_id = cancelled["result"]["session_id"]
    result = await _transfer_call(
        client,
        entry=entry,
        action="import_cancel",
        data={"session_id": cancelled_id},
    )
    assert result["success"] and result["result"]["cancelled"]
    session_a = await _upload_archive(
        client, entry=entry, archive=archive_a, filename=metadata_a["filename"]
    )
    session_b = await _upload_archive(
        client, entry=entry, archive=archive_b, filename=metadata_b["filename"]
    )
    assert session_a != session_b

    first = await _transfer_call(
        client, entry=entry, action="import_inspect", data={"session_id": session_a}
    )
    assert first["success"], first
    await memory.async_add(owner, "TARGET-CHANGED", "acceptance", "explicit")
    second = await _transfer_call(
        client, entry=entry, action="import_inspect", data={"session_id": session_b}
    )
    assert second["success"], second
    stale_a = await _transfer_call(
        client,
        entry=entry,
        action="import_restore",
        data={
            "session_id": session_a,
            "preview_token": first["result"]["preview_token"],
        },
    )
    assert not stale_a["success"]
    assert "stale" in stale_a["error"]["message"].lower()
    assert any(
        item.content == "TARGET-CHANGED" for item in await memory.async_list(owner)
    )

    await memory.async_add(owner, "TARGET-CHANGED-AGAIN", "acceptance", "explicit")
    stale_b = await _transfer_call(
        client,
        entry=entry,
        action="import_restore",
        data={
            "session_id": session_b,
            "preview_token": second["result"]["preview_token"],
        },
    )
    assert not stale_b["success"]
    assert "target changed" in stale_b["error"]["message"].lower()

    fresh = await _transfer_call(
        client, entry=entry, action="import_inspect", data={"session_id": session_b}
    )
    assert fresh["success"], fresh
    assert fresh["result"]["preview_token"] != second["result"]["preview_token"]
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    applied = await _transfer_call(
        client,
        entry=entry,
        action="import_restore",
        data={
            "session_id": session_b,
            "preview_token": fresh["result"]["preview_token"],
        },
    )
    assert applied["success"], applied
    assert applied["result"]["status"] == "restored"
    contents = {item.content for item in await memory.async_list(owner)}
    assert "BACKUP-A" in contents and "BACKUP-B" in contents
    assert "TARGET-CHANGED" not in contents
    assert "TARGET-CHANGED-AGAIN" not in contents

    # Expiration removes both session files and their preview authority.
    backup_transfer._imports(hass)[session_a].expires_at = (
        backup_transfer.time.monotonic() - 1
    )
    expired = await _transfer_call(
        client, entry=entry, action="import_inspect", data={"session_id": session_a}
    )
    assert not expired["success"]
    record(
        stress_trace,
        "summary",
        layer="Real HA",
        transfer_sessions=3,
        concurrent_import_sessions=2,
        transfer_previews=3,
        stale_apply_rejections=2,
        expired_import_sessions=1,
        cancelled_import_sessions=1,
    )
