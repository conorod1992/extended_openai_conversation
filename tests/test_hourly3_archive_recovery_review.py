"""Mixed legacy and current sessions retain order and privacy through recovery."""

from dataclasses import asdict, replace
import json

import pytest

from custom_components.extended_openai_conversation_responses import (
    backup,
    restore_recovery,
)
from custom_components.extended_openai_conversation_responses.agent_maintenance import (
    get_agent_maintenance_gate,
)
from custom_components.extended_openai_conversation_responses.conversation_archive import (
    ConversationArchive,
    HomeAssistantArchiveStorage,
)
from custom_components.extended_openai_conversation_responses.scope import user_scope
from tests.test_backup import _document
from tests.test_conversation_archive import FakeArchiveStorage, _session, _turn


@pytest.mark.parametrize("phase", ["applying", "committed"])
async def test_mixed_session_journal_roundtrip_keeps_order_private_boundary_and_resume(
    hass, phase
):
    original = FakeArchiveStorage()
    legacy = _session("legacy", turn_count=2)
    original.metadata = {
        "sessions": [asdict(legacy)],
        "active": {},
        "partitions": ["2026-09"],
    }
    original.partitions = {
        "2026-09": {
            "turns": [
                {
                    key: value
                    for key, value in asdict(
                        _turn(
                            "legacy",
                            turn_id=f"legacy-{index}",
                            user_text=f"Legacy {index}",
                        )
                    ).items()
                    if key != "sequence"
                }
                for index in [0, 1]
            ]
        }
    }
    source = ConversationArchive(original, "agent")
    await source.async_initialize()
    assert original.metadata_save_count == original.partition_save_count == 0
    modern = await source.async_begin_session(
        "modern",
        user_scope("alice", source="authenticated_user"),
        "modern-ha",
        archive_enabled=True,
        shared_archive_enabled=False,
        inactivity_minutes=30,
    )
    for index in [0, 1]:
        await source.async_record_turn(
            modern.session_id,
            run_id=f"modern-{index}",
            user_text=f"Modern {index}",
            assistant_text="Reply",
            successful=True,
        )
    private_key = "continuity:user:bob"
    private_scope = user_scope("bob", source="authenticated_user")
    private = await source.async_begin_session(
        private_key,
        private_scope,
        "private-ha",
        archive_enabled=True,
        shared_archive_enabled=False,
        inactivity_minutes=30,
    )
    await source.async_record_turn(
        private.session_id,
        run_id="private",
        user_text="Private material to purge",
        assistant_text="Private reply",
        successful=True,
    )
    await source.async_make_private(private.session_id)
    recovery = await source.async_recovery_data()
    assert "Private material" not in json.dumps(recovery)
    assert [
        turn.get("sequence")
        for turn in recovery["turns"]
        if turn["session_id"] == "legacy"
    ] == [None, None]
    assert [
        turn["sequence"]
        for turn in recovery["turns"]
        if turn["session_id"] == modern.session_id
    ] == [0, 1]

    prepared = backup.inspect_backup(_document(), "agent")
    sessions, turns = ConversationArchive.validate_backup_data(
        {key: recovery[key] for key in ["sessions", "turns"]},
        "agent",
        local_recovery=True,
    )
    prepared = replace(
        prepared,
        archive_sessions=sessions,
        archive_turns=turns,
        archive_active=recovery["active"],
    )
    journal = restore_recovery._new_journal("entry", "agent", prepared, prepared)
    journal["phase"] = phase
    loaded, target, rollback = await restore_recovery._async_load_journal(
        json.loads(json.dumps(journal)), "entry", "agent"
    )
    selected = target if loaded["phase"] == "committed" else rollback
    storage = HomeAssistantArchiveStorage(hass, "entry", "agent")
    gate = get_agent_maintenance_gate(hass, "entry", "agent")
    async with gate.exclusive():
        gate.recovery_required = True
        with gate.recovery_work():
            await storage.async_stage_journal_recovery(
                selected.archive_sessions,
                selected.archive_turns,
                selected.archive_active,
            )
        gate.recovery_required = False
    restored = ConversationArchive(storage, "agent")
    await restored.async_initialize()
    for session_id, prefix in [("legacy", "Legacy"), (modern.session_id, "Modern")]:
        result = await restored.async_get("user:alice", session_id)
        assert [turn["user_text"] for turn in result["turns"]] == [
            f"{prefix} 0",
            f"{prefix} 1",
        ]
        assert [turn["sequence"] for turn in result["turns"]] == [0, 1]
    assert restored.private_continuity_bindings() == {"user:bob": "private-ha"}
    assert (
        await restored.async_record_turn(
            private.session_id,
            run_id="still-private",
            user_text="Never persist this",
            assistant_text="Private",
            successful=True,
        )
        is None
    )
    resumed = await restored.async_resume_saving(
        private_key, private.session_id, private_scope, shared_archive_enabled=False
    )
    await restored.async_record_turn(
        resumed.session_id,
        run_id="resumed",
        user_text="Explicitly resumed",
        assistant_text="Retained",
        successful=True,
    )
    fresh = ConversationArchive(
        HomeAssistantArchiveStorage(hass, "entry", "agent"), "agent"
    )
    await fresh.async_initialize()
    public = await fresh.async_backup_data()
    assert "Never persist this" not in json.dumps(public)
    assert "Private material" not in json.dumps(public)
    assert fresh._sessions[private.session_id].retention_state == "private"
    assert fresh._sessions[private.session_id].turn_count == 0
    resumed_turns = (await fresh.async_get("user:bob", resumed.session_id))["turns"]
    assert [turn["sequence"] for turn in resumed_turns] == [0]
    assert [turn["user_text"] for turn in resumed_turns] == ["Explicitly resumed"]
