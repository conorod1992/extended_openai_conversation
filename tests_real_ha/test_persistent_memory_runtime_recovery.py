"""Recover invalidated Memory through ordinary Assist, using durable storage."""

import json

import pytest

from custom_components.extended_openai_conversation_responses import memory
from custom_components.extended_openai_conversation_responses.agent_config import (
    normalize_agent_config,
)
from custom_components.extended_openai_conversation_responses.const import (
    SUBSYSTEM_STATUS_KEY,
)
from homeassistant.components import conversation
from homeassistant.core import Context
from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry
from tests_real_ha.test_cross_feature_acceptance import _speech
from tests_real_ha.test_provider_input_history import _transport
from tests_real_ha.test_provider_wire_e2e import _chat_sse_text, _chat_sse_tool_call


class FaultStorage:
    """Inject storage boundary failures without replacing production methods."""

    def __init__(self, hass, path, commit):
        self.hass = hass
        self.path = path
        self.commit = commit
        self.outage = False
        self.reads = 0

    async def async_load(self):
        self.reads += 1
        if self.outage:
            raise OSError("Memory read unavailable")
        if not self.path.exists():
            return None
        return json.loads(await self.hass.async_add_executor_job(self.path.read_text))

    async def async_save(self, data):
        if not self.outage or self.commit:
            await self.hass.async_add_executor_job(
                self.path.write_text, json.dumps(data)
            )
        if self.outage:
            raise OSError("Memory write acknowledgement unavailable")


@pytest.mark.parametrize("commit", [False, True], ids=["before-commit", "lost-ack"])
async def test_loaded_memory_recovers_on_next_assist_without_config_change(
    hass, tmp_path, commit
):
    owner = await hass.auth.async_create_user("Memory owner")
    entry = _make_entry(
        include_ai_task=False,
        conversation_options=normalize_agent_config(
            {
                "chat_model": "gpt-5.6",
                "api_mode": "chat_completions",
                "reasoning_effort": "none",
                "functions": [],
                "memory_mode": "automatic",
                "temporary_memory": "off",
                "knowledge_enabled": False,
                "archive_enabled": False,
                "archive_model_search_enabled": False,
            }
        ),
    )
    subentry = next(iter(entry.subentries.values()))
    storage = FaultStorage(hass, tmp_path / "authoritative-memory.json", commit)
    manager = memory.PersistentMemory(storage)
    hass.data.setdefault(memory._MEMORY_MANAGERS, {})[
        (entry.entry_id, subentry.subentry_id)
    ] = manager
    await _setup_entry(hass, entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    await manager.async_add(owner.id, "My orchid is purple", "general", "explicit")

    async def ask(replies):
        async with _transport(entry, replies) as wire:
            result = await conversation.async_converse(
                hass=hass,
                text="Remember my orchid",
                context=Context(user_id=owner.id),
                language="en",
                agent_id=entry.entry_id,
                conversation_id=None,
            )
            assert _speech(result) == "Done"
            wire.assert_complete(len(replies))
            return wire

    await ask([_chat_sse_text("Done")])
    assert agent._memory is manager and manager.initialized
    original_config = subentry.data

    def status():
        return hass.data[SUBSYSTEM_STATUS_KEY][(entry.entry_id, subentry.subentry_id)][
            "persistent_memory"
        ]["status"]

    storage.outage = True
    with pytest.raises(OSError):
        await manager.async_add(owner.id, "My bicycle is red", "general", "explicit")
    assert not manager.initialized
    for _ in range(2):
        reads = storage.reads
        await ask([_chat_sse_text("Done")])
        assert storage.reads == reads + 1
        assert not manager.initialized
        assert status() == "failed"
    storage.outage = False
    reads = storage.reads
    wire = await ask(
        [
            _chat_sse_tool_call("recovered-list", "memory_list", {}),
            _chat_sse_text("Done"),
        ]
    )
    assert manager.initialized and agent._memory is manager
    assert subentry.data is original_config
    assert storage.reads == reads + 1
    assert status() == "healthy"
    expected = {"My orchid is purple"} | ({"My bicycle is red"} if commit else set())
    assert {item.content for item in await manager.async_list(owner.id)} == expected
    prompt = json.dumps(wire.requests[0]["body"])
    assert "My orchid is purple" in prompt
    output = next(
        item["content"]
        for item in wire.requests[1]["body"]["messages"]
        if item.get("role") == "tool"
    )
    for fact in expected:
        assert fact in output
    await ask([_chat_sse_text("Done")])
    assert storage.reads == reads + 1
