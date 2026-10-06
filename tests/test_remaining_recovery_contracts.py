"""Lost persistence acknowledgements and malformed remote data remain contained."""

from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import (
    model_catalog as catalog,
    model_catalog_manager as models,
    restore_recovery as recovery,
)
from custom_components.extended_openai_conversation_responses.agent_maintenance import (
    get_agent_maintenance_gate,
)
from custom_components.extended_openai_conversation_responses.conversation_archive import (
    ConversationArchive,
)
from custom_components.extended_openai_conversation_responses.functions.web import (
    _BoundedResponse,
)
from custom_components.extended_openai_conversation_responses.scope import user_scope
from homeassistant.exceptions import HomeAssistantError
from tests.test_conversation_archive import FakeArchiveStorage
from tests.test_model_catalog_refresh_failures import MemoryStore, _chunked_transport


@pytest.mark.parametrize("invalid", ["cycle", "unknown-parent"])
def test_catalog_snapshot_inheritance_rejects_cycles_and_unknown_parents(invalid):
    candidate = deepcopy(catalog.BUNDLED_CATALOG)
    first = deepcopy(candidate["models"][0])
    second = deepcopy(first)
    first.update(id="coverage-parent", display_name="Parent")
    second.update(
        id="coverage-child",
        display_name="Child",
        kind="snapshot",
        alias_of="coverage-parent",
    )
    if invalid == "cycle":
        first.update(kind="snapshot", alias_of="coverage-child")
    else:
        first.update(kind="alias", status="unknown")
    candidate["models"].extend([first, second])
    with pytest.raises(
        ValueError,
        match=r"Snapshot inheritance cycle|Invalid snapshot parent kind or status",
    ):
        catalog.validate_catalog(candidate)


async def test_incompatible_catalogue_status_survives_failed_status_save(
    hass, monkeypatch
):
    manager = models.ModelCatalogManager(hass)
    manager.store = MemoryStore()
    manager.store.fail = True
    candidate = deepcopy(catalog.BUNDLED_CATALOG)
    candidate["schema_version"] = 999
    candidate["catalog_version"] += 1
    _chunked_transport(monkeypatch, json.dumps(candidate).encode())
    result = await manager.async_check(force=True)
    assert result["source"] == "bundled"
    assert result["incompatible_catalog"]["schema_version"] == 999
    assert result["last_error"] is None
    assert manager.catalog is None and manager.available_catalog is None
    assert manager.etag is None
    assert manager.store.saved is None


async def test_unknown_http_status_is_rejected_without_reading_remote_body():
    response = SimpleNamespace(
        status=599, content=SimpleNamespace(readexactly=AsyncMock())
    )
    with pytest.raises(HomeAssistantError, match="HTTP 599 Unsuccessful response"):
        await _BoundedResponse(response, 100).read()
    response.content.readexactly.assert_not_awaited()


async def test_recovery_keeps_agent_quarantined_when_journal_readback_fails(
    hass, monkeypatch
):
    entry, subentry = (
        SimpleNamespace(entry_id="entry"),
        SimpleNamespace(subentry_id="agent"),
    )
    monkeypatch.setattr(
        recovery,
        "_journal_store",
        lambda *_: SimpleNamespace(
            async_load=AsyncMock(side_effect=OSError("journal unavailable"))
        ),
    )
    operation = AsyncMock(return_value="finished")
    assert (
        await recovery._async_recovery_operation(hass, entry, subentry, operation)
        == "finished"
    )
    gate = get_agent_maintenance_gate(hass, "entry", "agent")
    assert gate.recovery_required
    assert (
        hass.data[recovery.SUBSYSTEM_STATUS_KEY][("entry", "agent")][
            "restore_recovery"
        ]["status"]
        == "recovery_required"
    )


async def test_boot_recovery_continues_after_one_agent_failure(hass, monkeypatch):
    first, second = (
        SimpleNamespace(subentry_id="first", subentry_type="conversation"),
        SimpleNamespace(subentry_id="second", subentry_type="conversation"),
    )
    entry = SimpleNamespace(
        entry_id="entry", subentries={"first": first, "second": second}
    )
    hass.config_entries.async_entries.return_value = [entry]
    recover = AsyncMock(side_effect=[OSError("first journal unavailable"), None])
    monkeypatch.setattr(recovery, "async_recover_pending_restore", recover)
    await recovery.async_recover_pending_restores(hass)
    assert recover.await_count == 2
    assert recover.await_args.args == (hass, entry, second)


@pytest.mark.parametrize("readback", ["intent", "unrelated"])
async def test_archive_lost_metadata_acknowledgement_reconciles_exact_intent(readback):
    storage = FakeArchiveStorage()
    archive = ConversationArchive(storage, "agent")
    await archive.async_initialize()
    established = await archive.async_begin_session(
        "session-key",
        user_scope("alice", source="test"),
        "conversation",
        archive_enabled=True,
        shared_archive_enabled=False,
        inactivity_minutes=30,
    )
    original_save = storage.async_save_metadata

    async def lose_acknowledgement(value):
        storage.metadata = (
            deepcopy(value)
            if readback == "intent"
            else {"sessions": [], "active": {"unexpected": "other"}, "partitions": []}
        )
        raise OSError("metadata acknowledgement lost")

    storage.async_save_metadata = lose_acknowledgement
    with pytest.raises(OSError, match="metadata acknowledgement lost"):
        await archive.async_record_turn(
            established.session_id,
            run_id="run",
            user_text="question",
            assistant_text="answer",
            successful=True,
        )
    if readback == "intent":
        assert len(archive._sessions) == 1
        session = next(iter(archive._sessions.values()))
        assert session.scope_id == "user:alice"
        assert session.turn_count == 1
        storage.async_save_metadata = original_save
        restarted = ConversationArchive(storage, "agent")
        await restarted.async_initialize()
        assert restarted._sessions[session.session_id].scope_id == "user:alice"
        assert restarted._sessions[session.session_id].turn_count == 1
    else:
        assert not archive._initialized
        assert not archive._sessions
        with pytest.raises(RuntimeError, match="has not been initialized"):
            archive._ensure_initialized()
