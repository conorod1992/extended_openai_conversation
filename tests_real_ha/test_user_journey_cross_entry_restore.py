"""EOAI agent backup imported into another independently configured agent."""
from custom_components.extended_openai_conversation_responses import backup
from custom_components.extended_openai_conversation_responses.memory import async_get_memory
from custom_components.extended_openai_conversation_responses.knowledge import async_get_knowledge
from tests_real_ha.test_acceptance_lifecycle import (
    _make_entry, _setup_entry, _conversation_subentry,
)


async def test_backup_import_to_different_entry_preserves_target_credentials_and_data(hass):
    """Destination entry has distinct ID, provider credential, and private stores."""
    origin = _make_entry(
        "Journey source",
        include_ai_task=False,
        data={"api_key": "sk-origin", "skip_authentication": True},
    )
    target = _make_entry(
        "Journey destination",
        include_ai_task=False,
        data={"api_key": "sk-destination", "skip_authentication": True},
    )
    await _setup_entry(hass, origin)
    await _setup_entry(hass, target)
    source_sub = _conversation_subentry(origin)
    dest_sub = _conversation_subentry(target)
    assert origin.entry_id != target.entry_id
    assert source_sub.subentry_id != dest_sub.subentry_id

    source_memory = await async_get_memory(hass, origin.entry_id, source_sub.subentry_id)
    source_knowledge = await async_get_knowledge(hass, origin.entry_id, source_sub.subentry_id)
    await source_memory.async_add(
        "journey-owner", "The breaker is beside the back door.", "house", "explicit"
    )
    await source_knowledge.async_create(
        "Electrical safety note", "Breaker location",
        "The main breaker is beside the back door.",
    )
    payload = await backup.async_collect_backup_snapshot(hass, origin, source_sub)
    credentials_before = dict(target.data)
    result = await backup.async_restore_backup(hass, target, dest_sub, payload)
    assert result["status"] == "restored"
    assert dict(target.data) == credentials_before
    assert target.data["api_key"] == "sk-destination"
    restored_memory = await async_get_memory(hass, target.entry_id, dest_sub.subentry_id)
    restored_knowledge = await async_get_knowledge(hass, target.entry_id, dest_sub.subentry_id)
    assert any(
        item.content == "The breaker is beside the back door."
        for item in await restored_memory.async_list("journey-owner", limit=25)
    )
    assert any(
        "main breaker" in item["content"]
        for item in await restored_knowledge.async_list()
    )
    assert await hass.config_entries.async_reload(target.entry_id)
    await hass.async_block_till_done()
    reread = await async_get_memory(hass, target.entry_id, dest_sub.subentry_id)
    assert any(
        "breaker" in item.content
        for item in await reread.async_list("journey-owner", limit=25)
    )
