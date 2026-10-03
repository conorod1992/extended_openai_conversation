"""Sustained partitioned Archive writes, searches, and privacy transitions."""

from __future__ import annotations

import asyncio
import errno
import json
from pathlib import Path

import atomicwrites
import pytest

from custom_components.extended_openai_conversation_responses.conversation_archive import (
    ConversationArchive,
    HomeAssistantArchiveStorage,
    async_get_archive,
)
from custom_components.extended_openai_conversation_responses.scope import (
    shared_scope,
    unretained_scope,
    user_scope,
)
from homeassistant.core import HomeAssistant
from tests_real_ha.test_acceptance_lifecycle import (
    _conversation_subentry,
    _make_entry,
    _setup_entry,
)
from tests_stress.conftest import record
from tests_stress.test_os_storage_faults import real_store_io  # noqa: F401


@pytest.mark.asyncio
async def test_archive_scale_keeps_searches_within_their_retained_scope(
    hass: HomeAssistant,
    stress_scale: int,
    stress_trace: list[dict],
) -> None:
    entry = _make_entry("Living archive", include_ai_task=False)
    await _setup_entry(hass, entry)
    subentry = _conversation_subentry(entry)
    archive = await async_get_archive(hass, entry.entry_id, subentry.subentry_id)
    scopes = {
        "alice": user_scope("archive-alice", source="authenticated_user"),
        "bob": user_scope("archive-bob", source="authenticated_user"),
        "carol": user_scope("archive-carol", source="authenticated_user"),
        "shared": shared_scope(source="shared_voice_policy"),
        "unretained": unretained_scope(device_id="unmapped-satellite"),
    }
    sessions = {
        name: await archive.async_begin_session(
            f"archive-campaign-{name}",
            scope,
            f"ha-conversation-{name}",
            archive_enabled=True,
            shared_archive_enabled=True,
            inactivity_minutes=30,
        )
        for name, scope in scopes.items()
    }
    assert all(sessions.values())
    assert sessions["unretained"].retention_state == "unretained"

    per_scope = 50 * stress_scale
    retained = ("alice", "bob", "carol", "shared")
    for index in range(per_scope):
        await asyncio.gather(
            *(
                archive.async_record_turn(
                    sessions[name].session_id,
                    run_id=f"run-{name}-{index}",
                    user_text=f"{name}only{index:04d} archive question",
                    assistant_text=f"{name} archive answer {index}",
                    successful=True,
                )
                for name in retained
            )
        )
        if index % 10 == 0:
            for name in retained:
                own = await archive.async_search(
                    scopes[name].scope_id, f"{name}only{index:04d}"
                )
                assert own["results"]
                other = "bob" if name == "alice" else "alice"
                crossed = await archive.async_search(
                    scopes[other].scope_id, f"{name}only{index:04d}"
                )
                assert crossed["results"] == []

    assert (
        await archive.async_record_turn(
            sessions["unretained"].session_id,
            run_id="unretained-run",
            user_text="unretainedonlymarker",
            assistant_text="No archive",
            successful=True,
        )
        is None
    )
    assert archive.stats()["turn_count"] == per_scope * len(retained)

    # Deleting a retained history must immediately remove it from search, even
    # as other scopes continue receiving new turns.
    await asyncio.gather(
        archive.async_make_private(sessions["alice"].session_id),
        archive.async_record_turn(
            sessions["bob"].session_id,
            run_id="bob-after-private",
            user_text="bobafterprivatemarker",
            assistant_text="Bob still retained",
            successful=True,
        ),
    )
    assert (await archive.async_search(scopes["alice"].scope_id, "aliceonly"))[
        "results"
    ] == []
    assert (
        await archive.async_search(scopes["bob"].scope_id, "bobafterprivatemarker")
    )["results"]

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    archive = await async_get_archive(hass, entry.entry_id, subentry.subentry_id)
    assert archive.stats()["turn_count"] == per_scope * (len(retained) - 1) + 1
    for name in ("bob", "carol", "shared"):
        results = await archive.async_search(scopes[name].scope_id, f"{name}only0000")
        assert results["results"]
        assert all(
            result["session_id"] == sessions[name].session_id
            for result in results["results"]
        )
    assert (await archive.async_search(scopes["alice"].scope_id, "aliceonly"))[
        "results"
    ] == []
    record(
        stress_trace,
        "summary",
        layer="real-ha",
        archive_turns_written=per_scope * len(retained) + 1,
        archive_scopes=len(scopes),
        archive_privacy_transitions=1,
        archive_searches=(per_scope // 10) * len(retained) * 2 + 6,
    )


@pytest.mark.parametrize("boundary", ["before_replace", "after_replace"])
@pytest.mark.usefixtures("real_store_io")
async def test_archive_first_intent_failure_cannot_be_erased_by_session_publication(
    hass,
    monkeypatch,
    stress_trace,
    boundary,
):
    """An unacknowledged first intent remains authoritative across later mutations."""
    storage = HomeAssistantArchiveStorage(
        hass, "archive-intent-provider", "archive-intent-agent"
    )
    archive = ConversationArchive(storage, "archive-intent-agent")
    await archive.async_initialize()
    scopes = {
        name: user_scope(f"intent-owner-{name}", source="authenticated_user")
        for name in ("alpha", "beta", "gamma")
    }

    async def begin(manager, name):
        return await manager.async_begin_session(
            f"session-{name}",
            scopes[name],
            f"ha-{name}",
            archive_enabled=True,
            shared_archive_enabled=False,
            inactivity_minutes=30,
        )

    alpha = await begin(archive, "alpha")
    beta = await begin(archive, "beta")
    alpha_turn = await archive.async_record_turn(
        alpha.session_id,
        run_id="alpha-turn",
        user_text="ALPHA_PRIVATE_TRANSCRIPT",
        assistant_text="Alpha exact answer",
        successful=True,
    )
    beta_turn = await archive.async_record_turn(
        beta.session_id,
        run_id="beta-turn",
        user_text="BETA_PRESERVED_TRANSCRIPT",
        assistant_text="Beta exact answer",
        successful=True,
    )
    path = Path(storage._metadata.path)
    before = path.read_bytes()
    replace = atomicwrites._replace_atomic
    faults = []

    def fail(source, target):
        if Path(target) != path or faults:
            return replace(source, target)
        if boundary == "after_replace":
            replace(source, target)
            faults.append(json.loads(path.read_text())["data"])
        else:
            faults.append(None)
        raise OSError(errno.EIO, "Injected first intent acknowledgement failure")

    with monkeypatch.context() as fault:
        fault.setattr(atomicwrites, "_replace_atomic", fail)
        with pytest.raises(OSError):
            await archive.async_make_private(alpha.session_id)
    assert len(faults) == 1
    committed = boundary == "after_replace"
    if committed:
        intent = faults[0]
        intended = next(
            item
            for item in intent["sessions"]
            if item["session_id"] == alpha.session_id
        )
        assert intended["retention_state"] == "private" and intended["turn_count"] == 0
        assert intent["pending_partitions"]
        pending_turns = [
            turn
            for payload in intent["pending_partitions"].values()
            for turn in payload["turns"]
        ]
        assert [turn["turn_id"] for turn in pending_turns] == [beta_turn.turn_id]
        assert "ALPHA_PRIVATE_TRANSCRIPT" not in json.dumps(intent)
    else:
        assert path.read_bytes() == before
    record(
        stress_trace,
        "archive_first_intent_fault",
        boundary=boundary,
        actual_replace_completed=committed,
        fault_count=len(faults),
    )
    # This publication used to overwrite the committed journal using stale RAM.
    gamma = await begin(archive, "gamma")
    disk = json.loads(path.read_text())["data"]
    disk_alpha = next(
        item for item in disk["sessions"] if item["session_id"] == alpha.session_id
    )
    assert disk_alpha["retention_state"] == ("private" if committed else "retained"), (
        "Unrelated session publication erased authoritative archive intent"
    )
    if committed:
        assert archive._pending_partitions == set(intent["pending_partitions"])
        assert archive._sessions[alpha.session_id].retention_state == "private"
        assert archive._sessions[alpha.session_id].title == ""
    fresh = ConversationArchive(
        HomeAssistantArchiveStorage(
            hass, "archive-intent-provider", "archive-intent-agent"
        ),
        "archive-intent-agent",
    )
    await fresh.async_initialize()
    for manager in (archive, fresh):
        beta_data = await manager.async_get(scopes["beta"].scope_id, beta.session_id)
        assert [
            (turn["turn_id"], turn["user_text"], turn["assistant_text"])
            for turn in beta_data["turns"]
        ] == [(beta_turn.turn_id, "BETA_PRESERVED_TRANSCRIPT", "Beta exact answer")]
        assert beta_data["session"]["scope_id"] == scopes["beta"].scope_id
        with pytest.raises(ValueError):
            await manager.async_get(scopes["gamma"].scope_id, beta.session_id)
        assert (await manager.async_get(scopes["gamma"].scope_id, gamma.session_id))[
            "turns"
        ] == []
        if committed:
            with pytest.raises(ValueError):
                await manager.async_get(scopes["alpha"].scope_id, alpha.session_id)
            assert (
                await manager.async_search(
                    scopes["alpha"].scope_id, "ALPHA_PRIVATE_TRANSCRIPT"
                )
            )["results"] == []
        else:
            assert [
                turn["turn_id"]
                for turn in (
                    await manager.async_get(scopes["alpha"].scope_id, alpha.session_id)
                )["turns"]
            ] == [alpha_turn.turn_id]
    healthy = await archive.async_record_turn(
        beta.session_id,
        run_id="beta-healthy",
        user_text="BETA_HEALTHY_RETRY",
        assistant_text="Beta healthy answer",
        successful=True,
    )
    newest = ConversationArchive(
        HomeAssistantArchiveStorage(
            hass, "archive-intent-provider", "archive-intent-agent"
        ),
        "archive-intent-agent",
    )
    await newest.async_initialize()
    assert not archive._pending_partitions
    assert "pending_partitions" not in json.loads(path.read_text())["data"]
    if committed:
        assert "ALPHA_PRIVATE_TRANSCRIPT" not in path.read_text()
        assert "ALPHA_PRIVATE_TRANSCRIPT" not in json.dumps(
            await newest.async_backup_data()
        )
        for partition in newest._partitions:
            assert "ALPHA_PRIVATE_TRANSCRIPT" not in json.dumps(
                await newest._storage.async_load_partition(partition)
            )
    assert (await newest.async_get(scopes["beta"].scope_id, beta.session_id)) == (
        await archive.async_get(scopes["beta"].scope_id, beta.session_id)
    )
    assert [
        turn["turn_id"]
        for turn in (await newest.async_get(scopes["beta"].scope_id, beta.session_id))[
            "turns"
        ]
    ] == [beta_turn.turn_id, healthy.turn_id]
    record(
        stress_trace,
        "summary",
        layer="Real HA Store and atomic replacement",
        archive_first_intent_failure_cases=1,
        archive_first_intent_disk_commits=int(committed),
        archive_intent_reload_checks=1,
    )


async def _seed_monthly_archive(
    hass, entry_id, agent_id, count=3, scope_id="user:archive-owner"
):
    from dataclasses import asdict, replace

    from tests.test_conversation_archive import _session, _turn

    storage = HomeAssistantArchiveStorage(hass, entry_id, agent_id)
    sessions, turns = [], []
    for index in range(count):
        stamp = f"2026-{7 + index % 3:02d}-01T10:01:00+00:00"
        session = replace(
            _session(f"seed-{index}", scope_id=scope_id),
            agent_subentry_id=agent_id,
            started_at=stamp,
            last_message_at=stamp,
        )
        sessions.append(session)
        turns.append(
            _turn(
                session.session_id,
                turn_id=f"turn-{index}",
                timestamp=stamp,
                user_text=f"MONTHLY_ARCHIVE_MARKER {index}",
            )
        )
    for month in sorted({turn.timestamp[:7] for turn in turns}):
        await storage.async_save_partition(
            month,
            {
                "turns": [
                    asdict(turn) for turn in turns if turn.timestamp.startswith(month)
                ]
            },
        )
    await storage.async_save_metadata(
        {
            "sessions": [asdict(session) for session in sessions],
            "active": {"browser": sessions[0].session_id},
            "partitions": sorted({turn.timestamp[:7] for turn in turns}),
        }
    )
    return storage, sessions, turns


@pytest.mark.parametrize("month", ["2026-07", "2026-08", "2026-09"])
@pytest.mark.parametrize("interruption", ["read_failure", "cancel"])
@pytest.mark.usefixtures("real_store_io")
async def test_cached_archive_retries_native_partition_interruption(
    hass, monkeypatch, stress_trace, month, interruption
):
    import threading

    from custom_components.extended_openai_conversation_responses import (
        conversation_archive as archive_module,
    )
    from homeassistant.helpers import storage as ha_storage

    entry_id, agent_id = "retry-entry", "retry-agent"
    storage, sessions, turns = await _seed_monthly_archive(hass, entry_id, agent_id)
    for store in [
        storage._metadata,
        *(storage._partition_store(f"2026-{m}") for m in ("07", "08", "09")),
    ]:
        store._manager.async_invalidate(store.key)
    target = Path(storage._partition_store(month).path)
    reached, release = threading.Event(), threading.Event()
    load_json = ha_storage.json_util.load_json
    faults = []

    def interrupted_load(path, *args, **kwargs):
        if Path(path) == target and not faults:
            faults.append(month)
            if interruption == "read_failure":
                raise OSError(errno.EIO, "One-shot native archive read failure")
            reached.set()
            if not release.wait(10):
                raise RuntimeError("Native read was not released")
        return load_json(path, *args, **kwargs)

    with monkeypatch.context() as fault:
        fault.setattr(ha_storage.json_util, "load_json", interrupted_load)
        task = asyncio.create_task(async_get_archive(hass, entry_id, agent_id))
        try:
            if interruption == "cancel":
                async with asyncio.timeout(5):
                    while not reached.is_set():
                        await asyncio.sleep(0.01)
                task.cancel()
                await asyncio.sleep(0)
                release.set()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                with pytest.raises(OSError):
                    await task
        finally:
            release.set()
    manager = hass.data[archive_module._ARCHIVE_MANAGERS][(entry_id, agent_id)]
    assert not manager._initialized
    assert (
        not manager._sessions
        and not manager._turns
        and not manager._active
        and not manager._partitions
    )
    recovered = await async_get_archive(hass, entry_id, agent_id)
    assert recovered is manager
    fresh = ConversationArchive(
        HomeAssistantArchiveStorage(hass, entry_id, agent_id), agent_id
    )
    await fresh.async_initialize()
    snapshot = await manager.async_backup_data()
    assert snapshot == await fresh.async_backup_data()
    assert {turn["turn_id"] for turn in snapshot["turns"]} == {
        turn.turn_id for turn in turns
    }
    assert len(snapshot["turns"]) == 3
    ConversationArchive.validate_backup_data(snapshot, agent_id)
    assert await manager.async_search(
        "user:archive-owner", "MONTHLY_ARCHIVE_MARKER"
    ) == await fresh.async_search("user:archive-owner", "MONTHLY_ARCHIVE_MARKER")
    healthy = await manager.async_record_turn(
        sessions[0].session_id,
        run_id="healthy-retry",
        user_text="Healthy cached archive",
        assistant_text="Recovered",
        successful=True,
    )
    assert healthy is not None
    ConversationArchive.validate_backup_data(
        await manager.async_backup_data(), agent_id
    )
    record(
        stress_trace,
        "summary",
        cached_archive_retries=1,
        archive_native_read_interruptions=1,
        archive_fresh_reads=1,
        archive_turns_written=1,
    )


@pytest.mark.parametrize(
    "count,boundary",
    [
        (49, "healthy"),
        (50, "healthy"),
        (51, "healthy"),
        (200, "healthy"),
        (200, "before_replace"),
        (200, "after_replace"),
    ],
)
@pytest.mark.usefixtures("real_store_io")
async def test_public_archive_date_range_bulk_transaction(
    hass, monkeypatch, stress_trace, count, boundary
):
    from dataclasses import asdict, replace

    from custom_components.extended_openai_conversation_responses.management_ui import (
        async_management_command,
    )
    from tests.test_conversation_archive import _session, _turn

    entry = _make_entry("Bulk archive", include_ai_task=False)
    await _setup_entry(hass, entry)
    subentry = _conversation_subentry(entry)
    scope_id = "user:bulk-owner"
    storage, sessions, turns = await _seed_monthly_archive(
        hass, entry.entry_id, subentry.subentry_id, count, scope_id
    )
    controls = [
        replace(
            _session("other-owner", scope_id="user:sibling"),
            agent_subentry_id=subentry.subentry_id,
        ),
        replace(
            _session("private-control", turn_count=0, retention_state="private"),
            scope_id=scope_id,
            agent_subentry_id=subentry.subentry_id,
            title="",
        ),
        replace(
            _session("outside-control"),
            scope_id=scope_id,
            agent_subentry_id=subentry.subentry_id,
            started_at="2026-10-01T10:00:00+00:00",
            last_message_at="2026-10-01T10:01:00+00:00",
        ),
    ]
    for session in (controls[0], controls[2]):
        turns.append(_turn(session.session_id, turn_id=f"turn-{session.session_id}"))
    for month in sorted({turn.timestamp[:7] for turn in turns}):
        await storage.async_save_partition(
            month,
            {
                "turns": [
                    asdict(turn) for turn in turns if turn.timestamp.startswith(month)
                ]
            },
        )
    await storage.async_save_metadata(
        {
            "sessions": [asdict(session) for session in [*sessions, *controls]],
            "active": {"browser": sessions[0].session_id},
            "partitions": sorted({turn.timestamp[:7] for turn in turns}),
        }
    )
    # Reuse the already cached, not-yet-populated manager exactly as acquisition does.
    archive = await async_get_archive(hass, entry.entry_id, subentry.subentry_id)
    # Setup may have initialized an empty manager before the fixture seeded files.
    if not archive._sessions:
        archive._initialized = False
        await archive.async_initialize()
    ephemeral = await archive.async_begin_session(
        "ephemeral-control",
        unretained_scope(),
        None,
        archive_enabled=True,
        shared_archive_enabled=False,
        inactivity_minutes=30,
    )
    path = Path(storage._metadata.path)
    before = path.read_bytes()
    replace_atomic = atomicwrites._replace_atomic
    failures = []

    def fail_once(source, destination):
        if Path(destination) == path and not failures:
            failures.append(boundary)
            if boundary == "after_replace":
                replace_atomic(source, destination)
            raise OSError(errno.EIO, "Bulk archive intent failure")
        return replace_atomic(source, destination)

    async def delete():
        return await async_management_command(
            hass,
            "bulk-owner",
            True,
            {
                "entry_id": entry.entry_id,
                "subentry_id": subentry.subentry_id,
                "section": "conversations",
                "action": "delete_range",
                "scope_id": scope_id,
                "start_date": "2026-07-01",
                "end_date": "2026-09-30",
                "confirm": True,
            },
        )

    if boundary == "healthy":
        assert await delete() == {"deleted_sessions": count, "deleted_turns": count}
    else:
        with monkeypatch.context() as fault:
            fault.setattr(atomicwrites, "_replace_atomic", fail_once)
            with pytest.raises(OSError):
                await delete()
        if boundary == "before_replace":
            assert path.read_bytes() == before
            assert {session.session_id for session in sessions} <= set(
                archive._sessions
            )
            assert await delete() == {"deleted_sessions": count, "deleted_turns": count}
    assert set(archive._sessions) == {session.session_id for session in controls} | {
        ephemeral.session_id
    }
    assert not archive._active.get("browser")
    fresh = ConversationArchive(
        HomeAssistantArchiveStorage(hass, entry.entry_id, subentry.subentry_id),
        subentry.subentry_id,
    )
    await fresh.async_initialize()
    assert set(fresh._sessions) == {session.session_id for session in controls}
    assert {
        turn["session_id"] for turn in (await fresh.async_backup_data())["turns"]
    } == {controls[0].session_id, controls[2].session_id}
    ConversationArchive.validate_backup_data(
        await fresh.async_backup_data(), subentry.subentry_id
    )
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert (
        await (
            await async_get_archive(hass, entry.entry_id, subentry.subentry_id)
        ).async_list_sessions(scope_id)
    )["sessions"] == (await fresh.async_list_sessions(scope_id))["sessions"]
    record(
        stress_trace,
        "summary",
        archive_bulk_sessions_deleted=count,
        archive_bulk_deletions=1,
        archive_fresh_reads=1,
        archive_bulk_intent_faults=int(boundary != "healthy"),
    )


@pytest.mark.usefixtures("real_store_io")
async def test_public_private_archive_scrubs_metadata_and_full_backups(
    hass, monkeypatch, stress_trace
):

    from custom_components.extended_openai_conversation_responses import backup
    from custom_components.extended_openai_conversation_responses.const import (
        CONF_ARCHIVE_ENABLED,
    )
    from homeassistant.components import conversation
    from homeassistant.core import Context
    from tests_real_ha.test_cross_feature_acceptance import _agent
    from tests_real_ha.test_provider_wire_e2e import (
        _chat_sse_text,
        _chat_sse_tool_call,
        _install_wire,
    )

    agent = await _agent(hass, title="Private metadata", **{CONF_ARCHIVE_ENABLED: True})
    owner = await hass.auth.async_create_user("Archive privacy owner")
    wire = _install_wire(
        monkeypatch,
        agent,
        [
            _chat_sse_text("First answer"),
            _chat_sse_text("Sibling answer"),
            _chat_sse_tool_call("privacy-tool", "conversation_private", {}),
            _chat_sse_text("Private now"),
        ],
    )

    async def say(text, conversation_id=None):
        return await conversation.async_converse(
            hass=hass,
            text=text,
            conversation_id=conversation_id,
            context=Context(user_id=owner.id),
            language="en",
            agent_id=agent.entry.entry_id,
        )

    first = await say("PRIVATE_SUBJECT_MARKER")
    sibling = await say("SIBLING_SUBJECT_MARKER")
    assert first.response.error_code is sibling.response.error_code is None
    archive = agent._archive
    sessions = await archive.async_list_sessions(f"user:{owner.id}")
    private_id = next(
        item["session_id"]
        for item in sessions["sessions"]
        if item["title"] == "PRIVATE_SUBJECT_MARKER"
    )
    result = await say("Make this conversation private", first.conversation_id)
    assert result.response.error_code is None
    assert len(wire.requests) == 4
    assert archive._sessions[private_id].retention_state == "private"
    assert archive._sessions[private_id].title == ""

    async def assert_clean(manager):
        assert "PRIVATE_SUBJECT_MARKER" not in json.dumps(
            await manager.async_backup_data()
        )
        assert "SIBLING_SUBJECT_MARKER" in json.dumps(await manager.async_backup_data())
        assert (
            "PRIVATE_SUBJECT_MARKER"
            not in Path(manager._storage._metadata.path).read_text()
        )
        for month in manager._partitions:
            assert "PRIVATE_SUBJECT_MARKER" not in json.dumps(
                await manager._storage.async_load_partition(month)
            )

    await assert_clean(archive)
    full = await backup.async_create_backup(hass, agent.entry, agent.subentry)
    assert "PRIVATE_SUBJECT_MARKER" not in full["json"]
    assert "SIBLING_SUBJECT_MARKER" in full["json"]
    # Historical private titles are durably scrubbed on load; old exports cannot reintroduce them.
    metadata = await archive._storage.async_load_metadata()
    next(item for item in metadata["sessions"] if item["session_id"] == private_id)[
        "title"
    ] = "PRIVATE_SUBJECT_MARKER"
    await archive._storage.async_save_metadata(metadata)
    fresh = ConversationArchive(
        HomeAssistantArchiveStorage(
            hass, agent.entry.entry_id, agent.subentry.subentry_id
        ),
        agent.subentry.subentry_id,
    )
    await fresh.async_initialize()
    await assert_clean(fresh)
    document = full["document"]
    next(
        item
        for item in document["archive"]["sessions"]
        if item["session_id"] == private_id
    )["title"] = "PRIVATE_SUBJECT_MARKER"
    await backup.async_restore_backup(hass, agent.entry, agent.subentry, document)
    restored = await async_get_archive(
        hass, agent.entry.entry_id, agent.subentry.subentry_id
    )
    await assert_clean(restored)
    exported = await backup.async_create_backup(hass, agent.entry, agent.subentry)
    assert "PRIVATE_SUBJECT_MARKER" not in exported["json"]
    record(
        stress_trace,
        "summary",
        public_archive_privacy_transitions=1,
        archive_full_backups=2,
        legacy_private_title_migrations=1,
        archive_private_backup_restores=1,
    )
