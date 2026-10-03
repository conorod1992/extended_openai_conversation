"""Read restored contents through newly constructed managers and HA Stores."""

from custom_components.extended_openai_conversation_responses.knowledge import (
    HomeAssistantKnowledgeStorage,
    KnowledgeLibrary,
)
from custom_components.extended_openai_conversation_responses.memory import (
    HomeAssistantMemoryStorage,
    PersistentMemory,
)


async def assert_fresh_contents(hass, entry, subentry, expected):
    memory = PersistentMemory(
        HomeAssistantMemoryStorage(hass, entry.entry_id, subentry.subentry_id)
    )
    knowledge = KnowledgeLibrary(
        HomeAssistantKnowledgeStorage(hass, entry.entry_id, subentry.subentry_id)
    )
    await memory.async_initialize()
    await knowledge.async_initialize()
    assert await memory.async_backup_data() == expected["memories"]
    assert await knowledge.async_backup_data() == expected["knowledge"]
