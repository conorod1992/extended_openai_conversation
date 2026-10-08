"""Archive repair and canonical retrieval through native Home Assistant Store I/O."""

from dataclasses import asdict
import unicodedata

from custom_components.extended_openai_conversation_responses.const import CONF_ARCHIVE_ENABLED
from custom_components.extended_openai_conversation_responses.conversation_archive import ConversationArchive
from tests.test_conversation_archive import _session, _turn
from tests_real_ha.test_cross_feature_acceptance import _agent
from tests_stress.test_os_storage_faults import real_store_io  # noqa: F401


async def test_native_archive_salvages_records_repairs_counts_and_reloads_unicode(hass, real_store_io):
    agent = await _agent(hass, **{CONF_ARCHIVE_ENABLED: True})
    storage = agent._archive._storage
    content = unicodedata.normalize("NFD", "Café résumé belongs to Alice")
    await storage.async_save_metadata({
        "sessions": [asdict(_session("bad")) | {"session_id": []}, asdict(_session(turn_count=99))],
        "active": {"browser": "session-1"}, "partitions": ["2026-09"],
    })
    await storage.async_save_partition("2026-09", {"turns": [
        asdict(_turn(turn_id="bad")) | {"assistant_text": {}},
        asdict(_turn(user_text=content)),
        asdict(_turn(user_text="Duplicate must not replace the original")),
    ]})
    archive = ConversationArchive(storage, agent.subentry.subentry_id)
    await archive.async_initialize()
    assert archive.stats()["turn_count"] == 1
    assert archive.active_session("browser").turn_count == 1
    assert (await archive.async_search("user:alice", "café résumé"))["results"][0]["turn_id"] == "turn-1"
    backup = await archive.async_backup_data()
    assert backup["turns"][0]["user_text"] == content
    ConversationArchive.validate_backup_data(backup, agent.subentry.subentry_id)
    fresh = ConversationArchive(storage, agent.subentry.subentry_id)
    await fresh.async_initialize()
    assert await fresh.async_backup_data() == backup
    await fresh.async_delete_session("user:alice", "session-1")
    reloaded = ConversationArchive(storage, agent.subentry.subentry_id)
    await reloaded.async_initialize()
    assert reloaded.stats()["turn_count"] == 0
