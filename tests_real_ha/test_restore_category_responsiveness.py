"""Archive-heavy, Knowledge-heavy and mixed full restores remain responsive."""

import asyncio
from dataclasses import asdict, replace

import pytest
from pytest_homeassistant_custom_component.common import MockUser
from homeassistant.util import dt as dt_util

from custom_components.extended_openai_conversation_responses import backup
from custom_components.extended_openai_conversation_responses.conversation_archive import (
    async_get_archive,
)
from custom_components.extended_openai_conversation_responses.knowledge import (
    async_get_knowledge,
)
from custom_components.extended_openai_conversation_responses.memory import (
    async_get_memory,
)
from tests.test_backup import _document
from tests.test_conversation_archive import _session, _turn
from tests_stress.test_os_storage_faults import real_store_io  # noqa: F401
from tests_real_ha.test_backup_transfer_protocol import (
    _entry,
    _setup_entry,
    _conversation_subentry,
    _user_token,
    _download_archive,
    _upload_archive,
    _transfer_call,
)


@pytest.mark.timeout(180)
@pytest.mark.parametrize(
    "turns,sources,memories",
    [(6000, 0, 0), (0, 500, 0), (2000, 200, 5000)],
    ids=["archive-heavy", "knowledge-heavy", "mixed"],
)
async def test_category_restore_remains_responsive(
    hass, hass_ws_client, real_store_io, turns, sources, memories
):
    entry = _entry()
    await _setup_entry(hass, entry)
    subentry = _conversation_subentry(entry)
    # Seed through durable manager APIs; no delayed work or artificial CPU stalls.
    now = dt_util.utcnow().isoformat()
    archive = await async_get_archive(hass, entry.entry_id, subentry.subentry_id)
    sessions = (
        [
            replace(
                _session(turn_count=turns),
                agent_subentry_id=subentry.subentry_id,
                started_at=now,
                last_message_at=now,
            )
        ]
        if turns
        else []
    )
    records = [replace(_turn(turn_id=f"turn-{i}"), timestamp=now) for i in range(turns)]
    await archive.async_replace_backup(sessions, records)
    document = _document()
    knowledge = await async_get_knowledge(hass, entry.entry_id, subentry.subentry_id)
    await knowledge.async_replace_backup(
        backup.KnowledgeLibrary.validate_backup_data(
            {
                "sources": [
                    {
                        **document["knowledge"]["sources"][0],
                        "source_id": f"source-{i}",
                        "content": "House reference information. " * 350,
                    }
                    for i in range(sources)
                ]
            }
        )
    )
    memory = await async_get_memory(hass, entry.entry_id, subentry.subentry_id)
    await memory.async_replace_backup(
        backup.PersistentMemory.validate_backup_data(
            {
                "memories": [
                    {**document["memories"]["memories"][0], "memory_id": f"memory-{i}"}
                    for i in range(memories)
                ]
            }
        )
    )
    expected_knowledge = await knowledge.async_backup_data()
    expected_memories = await memory.async_backup_data()
    # These are actual saved owners, so import does not require scope remapping.
    MockUser(id="user-1", name="Memory owner").add_to_hass(hass)
    MockUser(id="alice", name="Archive owner").add_to_hass(hass)
    admin = MockUser(id="category-restore-owner", name="Restore owner", is_owner=True)
    client = await hass_ws_client(hass, await _user_token(hass, admin))
    exported, metadata = await _download_archive(client, entry=entry, mode="full")
    session = await _upload_archive(
        client, entry=entry, archive=exported, filename=metadata["filename"]
    )
    inspected = await _transfer_call(
        client, entry=entry, action="import_inspect", data={"session_id": session}
    )
    assert inspected["success"], inspected
    gaps = []
    running = True
    loop = asyncio.get_running_loop()
    previous_debug = loop.get_debug()
    loop.set_debug(False)

    async def heartbeat():
        previous = loop.time()
        while running:
            await asyncio.sleep(0.005)
            now = loop.time()
            gaps.append(now - previous)
            previous = now

    pulse = asyncio.create_task(heartbeat())
    await asyncio.sleep(0.01)
    try:
        restored = await _transfer_call(
            client,
            entry=entry,
            action="import_restore",
            data={
                "session_id": session,
                "preview_token": inspected["result"]["preview_token"],
            },
        )
        await asyncio.sleep(0.01)
    finally:
        running = False
        await pulse
        loop.set_debug(previous_debug)
    assert restored["success"], restored
    assert max(gaps) < 0.25, f"Category restore stalled HA for {max(gaps):.3f}s"
    # Assert exact content, not just restore success or record counts.
    assert await archive.async_backup_data() == {
        "sessions": [asdict(s) for s in sessions],
        "turns": [asdict(t) for t in records],
    }
    assert await knowledge.async_backup_data() == expected_knowledge
    assert await memory.async_backup_data() == expected_memories
