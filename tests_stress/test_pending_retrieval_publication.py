"""Pending real management writes must not publish failed data to public Assist."""

from __future__ import annotations

import asyncio
import errno
import json
from pathlib import Path

import atomicwrites
import pytest
from pytest_homeassistant_custom_component.common import MockUser

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
)
from custom_components.extended_openai_conversation_responses.knowledge import (
    HomeAssistantKnowledgeStorage,
    KnowledgeLibrary,
)
from custom_components.extended_openai_conversation_responses.memory import (
    HomeAssistantMemoryStorage,
    PersistentMemory,
)
from homeassistant.helpers.storage import Store
from tests_real_ha.test_knowledge_provider_wire_e2e import (
    _chat_sse_tool_call,
    _chat_tool_result,
    _knowledge_agent,
)
from tests_real_ha.test_management_backend_acceptance import (
    ADMIN_ID,
    _admin_client,
    _conversation_subentry,
    _management_call,
    _management_response,
)
from tests_real_ha.test_memory_provider_wire_e2e import _memory_agent
from tests_real_ha.test_provider_wire_e2e import _chat_sse_text, _install_wire, _speech
from tests_stress.conftest import record
from tests_stress.test_os_storage_faults import real_store_io  # noqa: F401
from tests_stress.test_provider_wire_privacy import _say


@pytest.mark.parametrize("kind", ["knowledge", "memory"])
@pytest.mark.usefixtures("real_store_io")
async def test_pending_failed_management_write_never_reaches_assist_provider(
    hass,
    hass_ws_client,
    monkeypatch,
    stress_trace,
    kind,
):
    if kind == "knowledge":
        agent = await _knowledge_agent(hass, API_MODE_CHAT_COMPLETIONS)
        entry = agent.entry
        manager = agent._knowledge
    else:
        entry, agent = await _memory_agent(hass, API_MODE_CHAT_COMPLETIONS)
        manager = agent._memory
    admin = await _admin_client(hass, hass_ws_client)
    other = MockUser(
        id="publication-owner-beta", name="Unaffected publication owner Beta"
    )
    other.add_to_hass(hass)
    section = "knowledge" if kind == "knowledge" else "memories"
    marker = f"CANDIDATE_ONLY_{kind.upper()}_ALPHA"
    old = f"COMMITTED_OLD_{kind.upper()}_ALPHA"
    unrelated = f"UNRELATED_{kind.upper()}_BETA"
    query = "publication calibration"
    subentry = _conversation_subentry(entry)
    if kind == "knowledge":
        baseline = await _management_call(
            admin,
            entry=entry,
            section=section,
            action="create",
            title="Unrelated Beta source",
            description="Always enabled baseline",
            content=unrelated,
            enabled=True,
        )
        created = await _management_call(
            admin,
            entry=entry,
            section=section,
            action="create",
            title="Alpha publication calibration",
            description="Alpha target reference",
            content=old,
            enabled=False,
        )
        target_id = created["source"]["source_id"]
        payload = {"source_id": target_id, "content": marker, "enabled": True}
        storage = manager._storage._store
    else:
        created = await _management_call(
            admin,
            entry=entry,
            section=section,
            action="add",
            content=f"{query}: {old}",
            category="projects",
        )
        listed = await _management_call(
            admin, entry=entry, section=section, action="list"
        )
        target_id = listed["memories"][0]["memory_id"]
        await _management_call(
            admin,
            entry=entry,
            section=section,
            action="add",
            scope_id=f"user:{other.id}",
            content=unrelated,
            category="projects",
        )
        payload = {"memory_id": target_id, "content": f"{query}: {marker}"}
        storage = manager._storage._store
    path = Path(storage.path)
    before = path.read_bytes()
    entered = asyncio.Event()
    release_write = asyncio.Event()
    entered_read = asyncio.Event()
    release_read = asyncio.Event()
    read_results = []
    write = Store._async_write_data
    search = manager.async_search
    fault_count = []
    replace = atomicwrites._replace_atomic

    async def held_write(store, data):
        if Path(store.path) == path and not entered.is_set():
            assert marker in json.dumps(data), (
                "Held write was not the submitted target mutation"
            )
            entered.set()
            await release_write.wait()
        await write(store, data)

    def fail_before_replace(source, target):
        if Path(target) == path and not fault_count:
            fault_count.append(True)
            raise OSError(errno.EIO, "Injected target mutation before durable commit")
        return replace(source, target)

    async def held_read(*args, **kwargs):
        entered_read.set()
        result = await search(*args, **kwargs)
        read_results.append(result)
        # Preserve the real result while controlling delivery after writer failure.
        await release_read.wait()
        return result

    call_id = f"publication-{kind}-lookup"
    tool = "knowledge_search" if kind == "knowledge" else "memory_search"
    wire = _install_wire(
        monkeypatch,
        agent,
        [
            _chat_sse_tool_call(call_id, tool, {"query": query, "limit": 5}),
            _chat_sse_text("Committed retrieval complete"),
        ],
    )
    writer = reader = None
    try:
        with monkeypatch.context() as fault:
            fault.setattr(Store, "_async_write_data", held_write)
            fault.setattr(atomicwrites, "_replace_atomic", fail_before_replace)
            fault.setattr(manager, "async_search", held_read)
            writer = asyncio.create_task(
                _management_response(
                    admin, entry=entry, section=section, action="update", **payload
                )
            )
            await asyncio.wait_for(entered.wait(), 10)
            assert path.read_bytes() == before
            reader = asyncio.create_task(
                _say(hass, entry.entry_id, ADMIN_ID, f"Find my {query} reference")
            )
            await asyncio.wait_for(entered_read.wait(), 10)
            record(
                stress_trace,
                "pending_public_read",
                kind=kind,
                target_write_entered=True,
                consumer_read_entered=True,
            )
            release_write.set()
            failed = await asyncio.wait_for(writer, 10)
            assert failed["success"] is False
            assert len(fault_count) == 1 and path.read_bytes() == before
            release_read.set()
            result = await asyncio.wait_for(reader, 10)
        assert _speech(result) == "Committed retrieval complete"
        assert len(wire.requests) == 2 and read_results
        tool_result = _chat_tool_result(wire.requests[1]["body"], call_id)
        serialized = json.dumps([request["body"] for request in wire.requests])
        assert marker not in serialized, (
            "Failed-before-commit candidate reached provider wire"
        )
        assert marker not in json.dumps(tool_result)
        if kind == "knowledge":
            fresh = KnowledgeLibrary(
                HomeAssistantKnowledgeStorage(
                    hass, entry.entry_id, subentry.subentry_id
                )
            )
            await fresh.async_initialize()
            live_target = await manager.async_get(target_id)
            fresh_target = await fresh.async_get(target_id)
            assert live_target == fresh_target
            assert live_target.content == old and live_target.enabled is False
            assert (
                await manager.async_get(baseline["source"]["source_id"])
            ).content == unrelated
            assert (
                await fresh.async_search(query)
                == await manager.async_search(query)
                == []
            )
        else:
            fresh = PersistentMemory(
                HomeAssistantMemoryStorage(hass, entry.entry_id, subentry.subentry_id)
            )
            await fresh.async_initialize()
            live = await manager.async_backup_data()
            durable = await fresh.async_backup_data()
            assert live == durable
            target = next(
                item for item in live["memories"] if item["memory_id"] == target_id
            )
            assert old in target["content"] and marker not in target["content"]
            assert (
                target["user_id"]
                != next(
                    item for item in live["memories"] if item["content"] == unrelated
                )["user_id"]
            )
            assert target_id in json.dumps(tool_result) and old in json.dumps(
                tool_result
            )
            assert unrelated not in serialized
        # Commit the exact formerly rejected mutation and retrieve it normally.
        saved = await _management_call(
            admin, entry=entry, section=section, action="update", **payload
        )
        assert saved
        healthy_id = f"publication-{kind}-healthy"
        healthy_wire = _install_wire(
            monkeypatch,
            agent,
            [
                _chat_sse_tool_call(healthy_id, tool, {"query": query, "limit": 5}),
                _chat_sse_text("Healthy committed retrieval"),
            ],
        )
        healthy = await _say(
            hass, entry.entry_id, ADMIN_ID, f"Find my {query} reference after commit"
        )
        assert _speech(healthy) == "Healthy committed retrieval"
        assert len(healthy_wire.requests) == 2
        healthy_tool = _chat_tool_result(healthy_wire.requests[1]["body"], healthy_id)
        assert marker in json.dumps(healthy_tool) and target_id in json.dumps(
            healthy_tool
        )
        assert marker in path.read_text()
        record(
            stress_trace,
            "summary",
            layer="Real management, atomic Store, public Assist and SDK wire",
            pending_publication_failure_cases=1,
            pending_publication_retry_checks=1,
            pending_publication_actual_tool_calls=2,
        )
    finally:
        release_write.set()
        release_read.set()
        for task in (writer, reader):
            if task is not None and not task.done():
                task.cancel()
        if writer is not None or reader is not None:
            await asyncio.gather(
                *(task for task in (writer, reader) if task is not None),
                return_exceptions=True,
            )
        await admin.close()
