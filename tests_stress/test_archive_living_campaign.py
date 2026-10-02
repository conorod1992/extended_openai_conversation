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
        assert "ALPHA_PRIVATE_TRANSCRIPT" not in json.dumps(
            intent["pending_partitions"]
        )
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
