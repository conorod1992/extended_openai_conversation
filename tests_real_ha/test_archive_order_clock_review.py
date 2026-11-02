"""Conversation order survives native disk reload and historical snapshot restore."""

from datetime import UTC, datetime
import json

import pytest

from custom_components.extended_openai_conversation_responses.conversation_archive import (
    ConversationArchive,
    HomeAssistantArchiveStorage,
)
from custom_components.extended_openai_conversation_responses.scope import user_scope
from homeassistant.util import dt as dt_util
from tests_stress.test_os_storage_faults import real_store_io  # noqa: F401


@pytest.mark.parametrize("legacy_snapshot", [False, True])
@pytest.mark.usefixtures("real_store_io")
async def test_native_archive_keeps_accepted_order_after_clock_correction(
    hass, monkeypatch, legacy_snapshot
):
    storage = HomeAssistantArchiveStorage(hass, "order-entry", "order-agent")
    archive = ConversationArchive(storage, "order-agent")
    await archive.async_initialize()
    now = datetime(2026, 11, 1, 0, 0, 5, tzinfo=UTC)
    monkeypatch.setattr(dt_util, "utcnow", lambda: now)
    session = await archive.async_begin_session(
        "browser",
        user_scope("alice", source="authenticated_user"),
        "conversation",
        archive_enabled=True,
        shared_archive_enabled=False,
        inactivity_minutes=30,
    )
    for run_id, completed_at in (
        ("first", now),
        ("second", datetime(2026, 10, 31, 23, 59, 45, tzinfo=UTC)),
    ):
        now = completed_at
        await archive.async_record_turn(
            session.session_id,
            run_id=run_id,
            user_text=f"{run_id} question",
            assistant_text=f"{run_id} reply",
            successful=True,
        )
    assert [
        turn["run_id"]
        for turn in (await archive.async_get("user:alice", session.session_id))["turns"]
    ] == ["first", "second"]

    if legacy_snapshot:
        snapshot = json.loads(json.dumps(await archive.async_backup_data()))
        # Historical backups have list order but no explicit per-turn sequence.
        for turn in snapshot["turns"]:
            turn.pop("sequence")
        sessions, turns = ConversationArchive.validate_backup_data(
            snapshot, "restored-agent"
        )
        storage = HomeAssistantArchiveStorage(hass, "order-entry", "restored-agent")
        restored = ConversationArchive(storage, "restored-agent")
        await restored.async_initialize()
        await restored.async_replace_backup(sessions, turns)

    # New Store owners must read the real monthly files rather than live objects.
    identity = "restored-agent" if legacy_snapshot else "order-agent"
    fresh = ConversationArchive(
        HomeAssistantArchiveStorage(hass, "order-entry", identity), identity
    )
    await fresh.async_initialize()
    loaded = (await fresh.async_get("user:alice", session.session_id))["turns"]
    assert [turn["run_id"] for turn in loaded] == ["first", "second"]
    assert [turn["sequence"] for turn in loaded] == [0, 1]
    assert loaded[0]["timestamp"] > loaded[1]["timestamp"]
