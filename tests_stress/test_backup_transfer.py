"""Large, genuine WebSocket chunk transfer and recovery campaign."""

from __future__ import annotations

import asyncio
import base64
from io import BytesIO
import random
import string
import zipfile

from pytest_homeassistant_custom_component.common import MockUser

from custom_components.extended_openai_conversation_responses import backup_transfer
from custom_components.extended_openai_conversation_responses.knowledge import (
    async_get_knowledge,
)
from custom_components.extended_openai_conversation_responses.memory import (
    async_get_memory,
)
from homeassistant.core import HomeAssistant
from tests_real_ha.test_backup_transfer_protocol import (
    _conversation_subentry,
    _entry,
    _setup_entry,
    _transfer_call as _real_transfer_call,
    _user_token,
)
from tests_stress.conftest import record

CHUNK_BYTES = 32 * 1024


async def test_large_multichunk_transfer_retry_and_restore(
    hass: HomeAssistant,
    hass_ws_client,
    monkeypatch,
    stress_seed: int,
    stress_trace: list[dict],
) -> None:
    # The production protocol supports smaller bounded chunks. Keeping each
    # WebSocket message below fixture-client limits also makes ordering faults
    # observable without relying on a very large single response frame.
    monkeypatch.setattr(backup_transfer, "BACKUP_CHUNK_BYTES", CHUNK_BYTES)
    entry = _entry()
    await _setup_entry(hass, entry)
    subentry = _conversation_subentry(entry)
    admin = MockUser(id="stress-transfer-admin", name="Transfer Admin", is_owner=True)
    client = await hass_ws_client(hass, await _user_token(hass, admin))

    async def _transfer_call(*args, **kwargs):
        # A stalled WebSocket operation must produce a bounded, attributed
        # failure rather than consume the entire campaign timeout.
        action = kwargs["action"]
        record(stress_trace, "transfer_request", action=action)
        return await asyncio.wait_for(_real_transfer_call(*args, **kwargs), 30)

    memory = await async_get_memory(hass, entry.entry_id, subentry.subentry_id)
    knowledge = await async_get_knowledge(hass, entry.entry_id, subentry.subentry_id)
    rng = random.Random(stress_seed ^ 0xBAACE)
    alphabet = string.ascii_letters + string.digits
    await memory.async_add(
        "transfer-owner", "ORIGINAL-TRANSFER-MEMORY", "acceptance", "explicit"
    )
    for index in range(10):
        payload = "".join(rng.choices(alphabet, k=30_000))
        await asyncio.wait_for(
            knowledge.async_create(
                f"Transfer source {index:02d}", "Large deterministic source", payload
            ),
            30,
        )
    record(stress_trace, "seed_large_archive", knowledge_sources=10)

    sessions = 0
    chunks = 0
    exported = await _transfer_call(
        client, entry=entry, action="export_start", data={"mode": "full"}
    )
    assert exported["success"], exported
    metadata = exported["result"]
    assert metadata["chunk_count"] >= 2, metadata
    sessions += 1
    archive_parts = []
    for index in range(metadata["chunk_count"]):
        response = await _transfer_call(
            client,
            entry=entry,
            action="export_chunk",
            data={"session_id": metadata["session_id"], "index": index},
        )
        assert response["success"], response
        archive_parts.append(
            base64.b64decode(response["result"]["data"], validate=True)
        )
        chunks += 1
    archive = b"".join(archive_parts)
    assert len(archive) == metadata["size"]
    with zipfile.ZipFile(BytesIO(archive)) as opened:
        assert opened.testzip() is None
        assert "backup.json" in opened.namelist()

    another = await _transfer_call(
        client, entry=entry, action="export_start", data={"mode": "full"}
    )
    assert another["success"], another
    assert another["result"]["session_id"] != metadata["session_id"]
    sessions += 1
    for session in (metadata["session_id"], another["result"]["session_id"]):
        cancelled = await _transfer_call(
            client, entry=entry, action="export_cancel", data={"session_id": session}
        )
        assert cancelled["success"], cancelled

    started = await _transfer_call(
        client,
        entry=entry,
        action="import_start",
        data={"filename": metadata["filename"], "size": len(archive)},
    )
    assert started["success"], started
    session = started["result"]["session_id"]
    sessions += 1
    first = base64.b64encode(archive[:CHUNK_BYTES]).decode("ascii")
    bad = await _transfer_call(
        client,
        entry=entry,
        action="import_chunk",
        data={"session_id": session, "index": 1, "data": first},
    )
    assert not bad["success"]
    assert "Expected backup chunk 0" in bad["error"]["message"]
    for index, offset in enumerate(range(0, len(archive), CHUNK_BYTES)):
        part = archive[offset : offset + CHUNK_BYTES]
        response = await _transfer_call(
            client,
            entry=entry,
            action="import_chunk",
            data={
                "session_id": session,
                "index": index,
                "data": base64.b64encode(part).decode("ascii"),
            },
        )
        assert response["success"], response
        chunks += 1
        if index == 0:
            duplicate = await _transfer_call(
                client,
                entry=entry,
                action="import_chunk",
                data={"session_id": session, "index": 0, "data": first},
            )
            assert not duplicate["success"]
            assert "Expected backup chunk 1" in duplicate["error"]["message"]

    preview = await _transfer_call(
        client, entry=entry, action="import_inspect", data={"session_id": session}
    )
    assert preview["success"], preview
    assert preview["result"]["valid"] is True
    assert preview["result"]["summary"]["persistent_memories"] == 1
    await memory.async_add(
        "transfer-owner", "MUTATED-AFTER-EXPORT", "acceptance", "explicit"
    )
    restored = await _transfer_call(
        client, entry=entry, action="import_restore", data={"session_id": session}
    )
    assert restored["success"], restored
    assert restored["result"]["status"] == "restored"
    actual = await memory.async_list("transfer-owner", limit=500)
    assert len(actual) == 1
    assert all("MUTATED-AFTER-EXPORT" not in item.content for item in actual)
    record(
        stress_trace,
        "summary",
        layer="Real HA",
        transfer_sessions=sessions,
        backup_chunks_transferred=chunks,
        backup_archive_bytes=len(archive),
        persistent_memories=len(actual),
        knowledge_sources=10,
        rollback_phases=0,
    )
