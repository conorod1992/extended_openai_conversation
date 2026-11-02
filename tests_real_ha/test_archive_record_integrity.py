"""Archive repair and canonical retrieval through native Home Assistant Store I/O."""

from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
import unicodedata

import pytest

from custom_components.extended_openai_conversation_responses.agent_maintenance import (
    get_agent_maintenance_gate,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_ARCHIVE_ENABLED,
)
from custom_components.extended_openai_conversation_responses.conversation_archive import (
    ConversationArchive,
    HomeAssistantArchiveStorage,
)
from custom_components.extended_openai_conversation_responses.persistence_hardening import (
    StorageRepairRequired,
)
from custom_components.extended_openai_conversation_responses.storage_recovery_diagnostics import (
    storage_recovery_snapshot,
)
from tests.test_conversation_archive import _session, _turn
from tests_real_ha.test_cross_feature_acceptance import _agent
from tests_stress.test_os_storage_faults import real_store_io  # noqa: F401


async def test_native_archive_salvages_records_repairs_counts_and_reloads_unicode(
    hass, real_store_io
):
    agent = await _agent(hass, **{CONF_ARCHIVE_ENABLED: True})
    storage = agent._archive._storage
    content = unicodedata.normalize("NFD", "Café résumé belongs to Alice")
    await storage.async_save_metadata(
        {
            "sessions": [
                asdict(_session("bad")) | {"session_id": []},
                asdict(_session(turn_count=99)),
            ],
            "active": {"browser": "session-1"},
            "partitions": ["2026-09"],
        }
    )
    await storage.async_save_partition(
        "2026-09",
        {
            "turns": [
                asdict(_turn(turn_id="bad")) | {"assistant_text": {}},
                asdict(_turn(user_text=content)),
                asdict(_turn(user_text="Duplicate must not replace the original")),
            ]
        },
    )
    archive = ConversationArchive(storage, agent.subentry.subentry_id)
    await archive.async_initialize()
    assert archive.stats()["turn_count"] == 1
    assert archive.active_session("browser").turn_count == 1
    assert (await archive.async_search("user:alice", "café résumé"))["results"][0][
        "turn_id"
    ] == "turn-1"
    original = deepcopy(
        (
            await storage.async_load_metadata(),
            await storage.async_load_partition("2026-09"),
        )
    )
    with pytest.raises(ValueError, match="needs repair"):
        await archive.async_backup_data()
    with pytest.raises(ValueError, match="needs repair"):
        await archive.async_delete_session("user:alice", "session-1")
    fresh = ConversationArchive(storage, agent.subentry.subentry_id)
    await fresh.async_initialize()
    assert (await fresh.async_search("user:alice", "caf\u00e9 r\u00e9sum\u00e9"))[
        "results"
    ][0]["turn_id"] == "turn-1"
    assert (
        await storage.async_load_metadata(),
        await storage.async_load_partition("2026-09"),
    ) == original


@pytest.mark.usefixtures("real_store_io")
async def test_native_archive_quarantine_can_be_replaced_under_journal_authority(hass):
    storage = HomeAssistantArchiveStorage(hass, "archive-entry", "archive-agent")
    await storage.async_save_partition(
        "2026-08",
        {
            "turns": [
                asdict(
                    _turn(
                        timestamp="2026-08-01T10:00:00+00:00",
                        user_text="Unselected old content",
                    )
                )
            ]
        },
    )
    await storage.async_save_metadata({"partitions": ["2026-08"]})
    metadata = Path(storage._metadata.path)
    rejected_bytes = b"{unreadable archive metadata"
    await hass.async_add_executor_job(metadata.write_bytes, rejected_bytes)
    # Let HA itself quarantine invalid JSON before EOAI recognizes its evidence.
    with pytest.raises(StorageRepairRequired):
        await storage.async_load_metadata()

    def evidence():
        return {
            path.name: path.read_bytes()
            for path in metadata.parent.glob(metadata.name + ".corrupt.*")
        }

    rejected = await hass.async_add_executor_job(evidence)
    assert list(rejected.values()) == [rejected_bytes]
    report = await hass.async_add_executor_job(
        storage_recovery_snapshot, str(metadata.parent), "archive-entry", "archive-agent"
    )
    assert report["stores"]["archive"]["unavailable_stores"] == 1
    assert report["restore_journal"]["state"] == "absent"
    assert await hass.async_add_executor_job(evidence) == rejected
    gate = get_agent_maintenance_gate(hass, "archive-entry", "archive-agent")
    async with gate.exclusive():
        gate.recovery_required = True
        with gate.recovery_work():
            await storage.async_stage_journal_recovery(
                [_session()], [_turn(user_text="Selected journal content")], {}
            )
        gate.recovery_required = False

    fresh_storage = HomeAssistantArchiveStorage(hass, "archive-entry", "archive-agent")
    fresh = ConversationArchive(fresh_storage, "archive-agent")
    await fresh.async_initialize()
    result = await fresh.async_get("user:alice", "session-1")
    assert [turn["user_text"] for turn in result["turns"]] == [
        "Selected journal content"
    ]
    assert [turn["sequence"] for turn in result["turns"]] == [0]
    assert await fresh_storage.async_load_partition("2026-08") == {"turns": []}
    assert "pending_partitions" not in await fresh_storage.async_load_metadata()
    report = await hass.async_add_executor_job(
        storage_recovery_snapshot, str(metadata.parent), "archive-entry", "archive-agent"
    )
    assert report["stores"]["archive"]["unavailable_stores"] == 0
    assert report["stores"]["archive"]["preserved_corrupt_files"] == 1
    assert await hass.async_add_executor_job(evidence) == rejected
