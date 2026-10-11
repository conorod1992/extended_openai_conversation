"""Privacy remains live through failed writes; every archived character is pageable."""

from datetime import UTC, datetime, timedelta
import json

import pytest

from custom_components.extended_openai_conversation_responses import (
    agent_deletion,
    conversation_archive as module,
)
from custom_components.extended_openai_conversation_responses.const import DOMAIN
from custom_components.extended_openai_conversation_responses.debug import DebugManager
from custom_components.extended_openai_conversation_responses.runtime_hardening import (
    bounded_tool_result_text,
)
from custom_components.extended_openai_conversation_responses.scope import user_scope
from tests.test_archive_session_hardening import FailingArchiveStorage, _archive


@pytest.mark.parametrize("completion", [False, True])
async def test_private_activity_survives_metadata_failure(monkeypatch, completion):
    storage = FailingArchiveStorage()
    archive = await _archive(storage)
    now = datetime(2026, 10, 11, 10, tzinfo=UTC)
    monkeypatch.setattr(module.dt_util, "utcnow", lambda: now)
    scope = user_scope("alice", source="test")

    async def begin():
        return await archive.async_begin_session(
            "key",
            scope,
            "conversation",
            archive_enabled=True,
            shared_archive_enabled=False,
            inactivity_minutes=30,
        )

    original = await begin()
    await archive.async_make_private(original.session_id)
    storage.fail_metadata = True
    now += timedelta(minutes=20)
    with pytest.raises(OSError):
        if completion:
            await archive.async_record_turn(
                original.session_id,
                run_id=None,
                user_text="private",
                assistant_text="secret",
                successful=True,
            )
        else:
            await begin()
    storage.fail_metadata = False
    now += timedelta(minutes=15)
    active = await begin()
    assert active.session_id == original.session_id
    assert active.retention_state == "private"
    await archive.async_record_turn(
        active.session_id,
        run_id=None,
        user_text="still private",
        assistant_text="secret",
        successful=True,
    )
    assert archive._turns.get(active.session_id, []) == []
    assert "secret" not in json.dumps(storage.metadata)


@pytest.mark.parametrize(
    "text", ["x" * 37439, ('字🙂"\\\n' * 7900)], ids=["ascii", "unicode-escaped"]
)
async def test_large_turn_pages_reassemble_without_wrapper_truncation(text):
    archive = await _archive(FailingArchiveStorage())
    session = await archive.async_begin_session(
        "key",
        user_scope("alice", source="test"),
        "conversation",
        archive_enabled=True,
        shared_archive_enabled=False,
        inactivity_minutes=30,
    )
    turn = await archive.async_record_turn(
        session.session_id,
        run_id=None,
        user_text=text,
        assistant_text=text,
        successful=True,
    )
    user, assistant = [], []
    index = offset = 0
    for _ in range(100):
        page = await archive.async_get(
            "user:alice", session.session_id, index, text_offset=offset
        )
        serialized = json.dumps(page, ensure_ascii=False)
        assert json.loads(bounded_tool_result_text(serialized)) == page
        user.extend(item["user_text"] for item in page["turns"])
        assistant.extend(item["assistant_text"] for item in page["turns"])
        if not page["has_more"]:
            break
        assert (page["next_start_turn"], page["next_text_offset"]) != (index, offset)
        index, offset = page["next_start_turn"], page["next_text_offset"]
    else:
        pytest.fail("cursor failed to finish")
    assert "".join(user) == turn.user_text
    assert "".join(assistant) == turn.assistant_text
    with pytest.raises(ValueError):
        await archive.async_get("user:bob", session.session_id, text_offset=1)
    await archive.async_make_private(session.session_id)
    with pytest.raises(ValueError):
        await archive.async_get("user:alice", session.session_id, text_offset=1)


async def test_permanent_deletion_clears_debug_manager(hass, monkeypatch):
    manager = DebugManager()
    manager.configure(enabled=True)
    trace = manager.begin(
        entry_id="entry",
        subentry_id="agent",
        user_input={"text": "secret"},
        incoming_conversation_id=None,
    )
    manager.finish(trace, successful=True, result="private")
    key = ("entry", "agent")
    other = DebugManager()
    hass.data[f"{DOMAIN}.request_debug_managers"] = {
        key: manager,
        ("entry", "other"): other,
    }
    monkeypatch.setattr(agent_deletion, "_storage_names", lambda *_: [])
    await agent_deletion.async_delete_agent_data(hass, *key)
    assert manager.summaries() == []
    assert key not in hass.data[f"{DOMAIN}.request_debug_managers"]
    assert hass.data[f"{DOMAIN}.request_debug_managers"][("entry", "other")] is other
