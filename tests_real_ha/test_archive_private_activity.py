"""Private Archive activity through Home Assistant's conversation entry point."""

from datetime import timedelta
import json

import pytest
from pytest_homeassistant_custom_component.common import MockUser

from custom_components.extended_openai_conversation_responses import backup, transfer
from custom_components.extended_openai_conversation_responses.const import (
    CONF_ARCHIVE_ENABLED,
    CONF_ARCHIVE_SESSION_TIMEOUT_MINUTES,
    CONF_CONVERSATION_CONTINUITY,
    CONF_SHARED_ARCHIVE_ENABLED,
    CONVERSATION_CONTINUITY_DEVICE,
    CONVERSATION_CONTINUITY_HA_DEFAULT,
    CONVERSATION_CONTINUITY_USER,
)
from homeassistant.components import conversation
from homeassistant.core import Context
from homeassistant.util import dt as dt_util
from tests_real_ha.test_cross_feature_acceptance import _agent, _provider, _speech
from tests_stress.test_os_storage_faults import real_store_io  # noqa: F401


async def test_continuing_private_conversation_never_resumes_retention(
    hass, monkeypatch
):
    agent = await _agent(
        hass,
        **{
            CONF_ARCHIVE_ENABLED: True,
            CONF_SHARED_ARCHIVE_ENABLED: True,
            CONF_ARCHIVE_SESSION_TIMEOUT_MINUTES: 30,
        },
    )
    _provider(monkeypatch, agent, ["Reply"] * 13)
    user = MockUser(id="archive-user", name="Archive User")
    user.add_to_hass(hass)

    async def say(text, conversation_id=None):
        return await conversation.async_converse(
            hass=hass,
            text=text,
            conversation_id=conversation_id,
            context=Context(user_id=user.id),
            language="en",
            agent_id=agent.entry.entry_id,
        )

    first = await say("Start this conversation")
    assert _speech(first) == "Reply"
    archive = agent._archive
    session = next(iter(archive._sessions.values()))
    assert session.turn_count == 1
    await archive.async_make_private(session.session_id)
    now = dt_util.utcnow()
    monkeypatch.setattr(dt_util, "utcnow", lambda: now)
    for _ in range(12):
        now += timedelta(minutes=10)
        result = await say("Private content", first.conversation_id)
        assert _speech(result) == "Reply"
        assert len(archive._sessions) == 1
        current = archive._sessions[session.session_id]
        assert current.retention_state == "private"
        assert current.turn_count == 0
    assert archive.stats()["turn_count"] == 0


@pytest.mark.usefixtures("real_store_io")
async def test_public_archive_privacy_resume_delete_survives_reload_and_preserves_other_session(
    hass, monkeypatch
):
    from custom_components.extended_openai_conversation_responses.conversation_archive import (
        ConversationArchive,
        HomeAssistantArchiveStorage,
    )
    from tests_real_ha.test_conversation_runtime_lifecycle import _tool

    user = MockUser(id="archive-owner", name="Archive Owner")
    user.add_to_hass(hass)
    agent = await _agent(hass, **{CONF_ARCHIVE_ENABLED: True})
    sent = _provider(
        monkeypatch,
        agent,
        [
            "Retained sibling.",
            "Initial retained.",
            _tool("conversation_private"),
            "Private reply.",
            "Private followup.",
            _tool("conversation_resume_saving"),
            "Resumed reply.",
            "Retained after resume.",
            _tool("conversation_delete_current"),
            "Deleted reply.",
            "New session reply.",
        ],
    )

    async def say(text, conversation_id=None):
        return await conversation.async_converse(
            hass=hass,
            text=text,
            conversation_id=conversation_id,
            context=Context(user_id=user.id),
            language="en",
            agent_id=agent.entry.entry_id,
        )

    sibling = await say("Separate retained conversation")
    assert _speech(sibling) == "Retained sibling."
    archive = agent._archive
    sibling_id = next(iter(archive._sessions))
    first = await say("Initial private candidate")
    assert _speech(first) == "Initial retained."
    original_id = next(key for key in archive._sessions if key != sibling_id)
    assert (
        _speech(await say("Make this private", first.conversation_id))
        == "Private reply."
    )
    assert (
        _speech(await say("Secret followup", first.conversation_id))
        == "Private followup."
    )
    private_backup = await archive.async_backup_data()
    assert archive._sessions[original_id].retention_state == "private"
    assert archive._sessions[original_id].turn_count == 0
    assert original_id not in archive._turns or not archive._turns[original_id]
    assert "Initial private candidate" not in str(private_backup)
    assert "Secret followup" not in str(private_backup)
    assert archive._sessions[sibling_id].turn_count == 1

    assert (
        _speech(await say("Resume saving", first.conversation_id)) == "Resumed reply."
    )
    resumed_id = next(
        key for key in archive._sessions if key not in {original_id, sibling_id}
    )
    assert archive._sessions[resumed_id].retention_state == "retained"
    assert archive._sessions[original_id].retention_state == "private"
    assert (
        _speech(await say("Retain new content", first.conversation_id))
        == "Retained after resume."
    )
    resumed_backup = await archive.async_backup_data()
    assert "Private reply." not in str(resumed_backup)
    assert "Secret followup" not in str(resumed_backup)
    assert "Retained after resume." in str(resumed_backup)
    assert (
        _speech(await say("Delete current", first.conversation_id)) == "Deleted reply."
    )
    assert resumed_id not in archive._sessions
    assert resumed_id not in archive._turns

    restored = ConversationArchive(
        HomeAssistantArchiveStorage(
            hass, agent.entry.entry_id, agent.subentry.subentry_id
        ),
        agent.subentry.subentry_id,
    )
    await restored.async_initialize()
    assert await restored.async_backup_data() == await archive.async_backup_data()
    assert resumed_id not in restored._sessions
    assert restored._sessions[sibling_id].turn_count == 1
    assert "Retained after resume." not in str(await restored.async_backup_data())
    assert (
        _speech(await say("Continue after deletion", first.conversation_id))
        == "New session reply."
    )
    assert resumed_id not in archive._sessions
    assert archive._sessions[sibling_id].turn_count == 1
    assert len(sent) == 11


@pytest.mark.usefixtures("real_store_io")
@pytest.mark.parametrize("api_mode", ["chat_completions", "responses"])
@pytest.mark.parametrize("mutation", ["private", "delete", "append"])
async def test_public_archive_search_never_publishes_revoked_worker_snapshot(
    hass, monkeypatch, api_mode, mutation
):
    """A real Assist search must not send concurrently revoked text to the SDK wire."""
    import asyncio
    import json
    from threading import Event

    from custom_components.extended_openai_conversation_responses import (
        conversation_archive as archive_module,
    )
    from custom_components.extended_openai_conversation_responses.const import (
        CONF_API_MODE,
        CONF_ARCHIVE_MODEL_SEARCH_ENABLED,
        CONF_CONVERSATION_CONTINUITY,
        CONVERSATION_CONTINUITY_HA_DEFAULT,
    )
    from tests_real_ha.test_provider_wire_e2e import (
        _chat_sse_text,
        _chat_sse_tool_call,
        _install_wire,
        _responses_sse_text,
        _responses_sse_tool_call,
    )

    owner = await hass.auth.async_create_user("Archive search owner")
    agent = await _agent(
        hass,
        **{
            CONF_ARCHIVE_ENABLED: True,
            CONF_ARCHIVE_MODEL_SEARCH_ENABLED: True,
            CONF_API_MODE: api_mode,
            CONF_CONVERSATION_CONTINUITY: CONVERSATION_CONTINUITY_HA_DEFAULT,
        },
    )
    text = _chat_sse_text if api_mode == "chat_completions" else _responses_sse_text

    def call(call_id):
        if api_mode == "chat_completions":
            return _chat_sse_tool_call(
                call_id, "conversation_search", {"query": "orchid"}
            )
        return _responses_sse_tool_call(
            call_id, "conversation_search", {"query": "orchid"}
        )

    wire = _install_wire(
        monkeypatch,
        agent,
        [
            text("Seed saved"),
            call("search-first"),
            text("Search finished"),
            call("search-next"),
            text("Next search finished"),
        ],
    )

    async def say(message):
        return await conversation.async_converse(
            hass=hass,
            text=message,
            conversation_id=None,
            context=Context(user_id=owner.id),
            language="en",
            agent_id=agent.entry.entry_id,
        )

    secret = "revoked-transcript-marker-6499"
    assert _speech(await say(f"orchid {secret}")) == "Seed saved"
    archive = agent._archive
    session_id = next(iter(archive._sessions))
    entered, release = Event(), Event()
    ranker = archive_module._search_archive_snapshot

    def blocked(*args):
        entered.set()
        assert release.wait(15)
        return ranker(*args)

    monkeypatch.setattr(archive_module, "_search_archive_snapshot", blocked)
    task = asyncio.create_task(say("Search stored conversations"))
    try:
        assert await asyncio.to_thread(entered.wait, 10)
        assert not archive._lock.locked()
        if mutation == "private":
            await archive.async_make_private(session_id)
        elif mutation == "delete":
            await archive.async_delete_session(f"user:{owner.id}", session_id)
        else:
            await archive.async_record_turn(
                session_id,
                run_id="later",
                user_text="later",
                assistant_text="later",
                successful=True,
            )
        release.set()
        assert _speech(await task) == "Search finished"
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert len(wire.requests) == 3
    continuation = json.dumps(wire.requests[2]["body"])
    if mutation == "append":
        assert secret in continuation
    else:
        assert secret not in continuation
        assert "Conversation Archive is temporarily unavailable" in continuation
    # A fresh public request must recover without returning the revoked text.
    assert _speech(await say("Search again")) == "Next search finished"
    assert len(wire.requests) == 5
    if mutation != "append":
        assert secret not in json.dumps(wire.requests[4]["body"])
    backup = await archive.async_backup_data()
    if mutation != "append":
        assert secret not in json.dumps(backup)


@pytest.mark.usefixtures("real_store_io")
@pytest.mark.parametrize("action", ["search", "list"])
@pytest.mark.parametrize("mutation", ["private", "delete"])
async def test_management_archive_snapshot_rechecks_revocation_on_real_websocket(
    hass, hass_ws_client, monkeypatch, action, mutation
):
    """Management keeps the same revocation fence after moving it into Archive."""
    import asyncio
    from threading import Event

    from custom_components.extended_openai_conversation_responses import (
        management_history_queries as history,
    )
    from custom_components.extended_openai_conversation_responses.scope import (
        user_scope,
    )
    from tests_real_ha.test_management_backend_acceptance import (
        _admin_client,
        _management_response,
    )

    agent = await _agent(hass, **{CONF_ARCHIVE_ENABLED: True})
    owner_id = "archive-query-admin"
    client = await _admin_client(hass, hass_ws_client, user_id=owner_id)
    archive = agent._archive
    session = await archive.async_begin_session(
        "archive-query",
        user_scope(owner_id, source="authenticated_user"),
        "archive-query",
        archive_enabled=True,
        shared_archive_enabled=False,
        inactivity_minutes=30,
    )
    await archive.async_record_turn(
        session.session_id,
        run_id="seed",
        user_text="orchid secret-marker",
        assistant_text="Saved",
        successful=True,
    )
    entered, release = Event(), Event()
    name = "_archive_search_sync" if action == "search" else "_archive_list_sync"
    ranker = getattr(history, name)

    def blocked(*args):
        entered.set()
        assert release.wait(15)
        return ranker(*args)

    monkeypatch.setattr(history, name, blocked)
    payload = {"query": "orchid"} if action == "search" else {}
    task = asyncio.create_task(
        _management_response(
            client, entry=agent.entry, section="conversations", action=action, **payload
        )
    )
    try:
        assert await asyncio.to_thread(entered.wait, 10)
        assert not archive._lock.locked()
        if mutation == "private":
            await archive.async_make_private(session.session_id)
        else:
            await archive.async_delete_session(f"user:{owner_id}", session.session_id)
        release.set()
        response = await task
        assert response["success"] is False
        assert "Archive changed during search" in response["error"]["message"]
        assert "secret-marker" not in str(response)
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    fresh = await _management_response(
        client, entry=agent.entry, section="conversations", action=action, **payload
    )
    assert fresh["success"] is True
    assert not fresh["result"]["results" if action == "search" else "sessions"]


@pytest.mark.parametrize("fail_knowledge", [False, True])
async def test_private_browser_followup_survives_knowledge_restore(
    hass, monkeypatch, fail_knowledge
):
    from custom_components.extended_openai_conversation_responses import (
        backup,
        transfer,
    )
    from custom_components.extended_openai_conversation_responses.knowledge import (
        async_get_knowledge,
    )

    agent = await _agent(
        hass, **{CONF_ARCHIVE_ENABLED: True, "conversation_continuity": "ha_default"}
    )
    _provider(monkeypatch, agent, ["Before", "Private followup"])
    user = MockUser(id="selective-restore-user", name="Archive User")
    user.add_to_hass(hass)

    async def say(text, conversation_id=None):
        return await conversation.async_converse(
            hass=hass,
            text=text,
            conversation_id=conversation_id,
            context=Context(user_id=user.id),
            language="en",
            agent_id=agent.entry.entry_id,
        )

    first = await say("Start browser conversation")
    archive = agent._archive
    session = next(iter(archive._sessions.values()))
    await archive.async_make_private(session.session_id)
    imported = transfer.PreparedTransfer(
        source_kind="custom_backup",
        mode="custom",
        title="Knowledge",
        available_sections=frozenset({transfer.SECTION_KNOWLEDGE}),
        created_at=None,
        integration_version=None,
        knowledge=[],
    )
    knowledge = await async_get_knowledge(
        hass, agent.entry.entry_id, agent.subentry.subentry_id
    )
    replace = knowledge.async_replace_backup
    writes = 0

    async def fail_once(*args, **kwargs):
        nonlocal writes
        writes += 1
        if writes == 1:
            raise OSError("controlled Knowledge failure")
        return await replace(*args, **kwargs)

    if fail_knowledge:
        monkeypatch.setattr(knowledge, "async_replace_backup", fail_once)
        with pytest.raises(
            backup.BackupError, match="previous agent state was recovered"
        ):
            await transfer.async_restore_transfer(
                hass,
                agent.entry,
                agent.subentry,
                imported,
                sections=[transfer.SECTION_KNOWLEDGE],
            )
    else:
        await transfer.async_restore_transfer(
            hass,
            agent.entry,
            agent.subentry,
            imported,
            sections=[transfer.SECTION_KNOWLEDGE],
        )
    result = await say("PRIVATE AFTER RESTORE", first.conversation_id)
    assert _speech(result) == "Private followup"
    assert result.conversation_id == first.conversation_id
    assert len(archive._sessions) == 1
    assert archive._sessions[session.session_id].retention_state == "private"
    assert archive.stats()["turn_count"] == 0
    assert "PRIVATE AFTER RESTORE" not in str(await archive.async_backup_data())


@pytest.mark.parametrize(
    "mode",
    [
        CONVERSATION_CONTINUITY_HA_DEFAULT,
        CONVERSATION_CONTINUITY_USER,
        CONVERSATION_CONTINUITY_DEVICE,
    ],
)
@pytest.mark.parametrize("rollback", [False, True])
async def test_private_followup_after_knowledge_restore(
    hass, monkeypatch, mode, rollback
):
    user = MockUser(id="restore-private-owner")
    user.add_to_hass(hass)
    agent = await _agent(
        hass, **{CONF_ARCHIVE_ENABLED: True, CONF_CONVERSATION_CONTINUITY: mode}
    )
    sent = _provider(
        monkeypatch,
        agent,
        [
            "Before restore reply",
            "Retained before reply",
            "Private after restore reply",
            "Retained after reply",
        ],
    )

    retained_user = MockUser(id="restore-retained-owner")
    retained_user.add_to_hass(hass)

    async def say(text, conversation_id=None, who=None):
        return await conversation.async_converse(
            hass=hass,
            text=text,
            conversation_id=conversation_id,
            context=Context(user_id=(who or user).id),
            device_id="retained-device" if who else "restore-device",
            language="en",
            agent_id=agent.entry.entry_id,
        )

    first = await say("Before restore content")
    assert first.response.error_code is None
    session = next(iter(agent._archive._sessions.values()))
    await agent._archive.async_make_private(session.session_id)
    retained_before = await say("Retained history witness", who=retained_user)
    assert retained_before.response.error_code is None
    document = transfer._new_transfer_document(agent.entry, agent.subentry, "custom")
    document["sections"][transfer.SECTION_KNOWLEDGE] = {"sources": []}
    if rollback:
        original = agent._knowledge.async_replace_backup
        calls = 0

        async def fail_first(records):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("injected Knowledge restore failure")
            await original(records)

        monkeypatch.setattr(agent._knowledge, "async_replace_backup", fail_first)
        with pytest.raises(
            backup.BackupError, match="previous agent state was recovered"
        ):
            await transfer.async_restore_transfer(
                hass, agent.entry, agent.subentry, document
            )
        assert calls == 2
    else:
        await transfer.async_restore_transfer(
            hass, agent.entry, agent.subentry, document
        )
    followup = await say("PRIVATE AFTER RESTORE", first.conversation_id)
    assert followup.response.error_code is None
    assert followup.conversation_id == first.conversation_id
    assert "PRIVATE AFTER RESTORE" not in json.dumps(
        await agent._archive.async_backup_data()
    )
    assert agent._archive._sessions[session.session_id].retention_state == "private"
    if mode != CONVERSATION_CONTINUITY_HA_DEFAULT:
        # Reusing a privacy identity must not resurrect provider context in Core's log.
        assert "Before restore content" not in json.dumps(sent[-1])
        assert "Before restore reply" not in json.dumps(sent[-1])

    retained_after = await say(
        "Retained followup", retained_before.conversation_id, retained_user
    )
    assert retained_after.response.error_code is None
    # Knowledge-only success and same-agent rollback both preserve the verified
    # retained bindings. Privacy identities still discard their provider context.
    assert retained_after.conversation_id == retained_before.conversation_id
    assert "Retained history witness" in json.dumps(sent[-1])
    assert "Before restore content" not in json.dumps(sent[-1])
    assert len(sent) == 4
    assert (await agent._knowledge.async_backup_data())["sources"] == []
