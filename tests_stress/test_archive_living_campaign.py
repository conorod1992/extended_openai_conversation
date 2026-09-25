"""Sustained partitioned Archive writes, searches, and privacy transitions."""

from __future__ import annotations

import asyncio

import pytest

from custom_components.extended_openai_conversation_responses.conversation_archive import (
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
