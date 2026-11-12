"""Confirmed manual Memory outcomes through the authenticated HA websocket."""

from unittest.mock import AsyncMock

from custom_components.extended_openai_conversation_responses.memory import (
    async_get_memory,
)
from tests_real_ha.test_management_backend_acceptance import (
    _admin_client,
    _conversation_subentry,
    _entry,
    _fresh_reload,
    _management_call,
    _management_response,
    _setup_entry,
)


async def test_manual_duplicate_metadata_outcomes_and_noop_write_through_real_ha(
    hass, hass_ws_client, monkeypatch
):
    entry = _entry("Memory focused fixes")
    await _setup_entry(hass, entry)
    client = await _admin_client(hass, hass_ws_client)
    base = dict(
        entry=entry,
        section="memories",
        content="Tea is kept in the pantry.",
        category="home",
    )
    created = await _management_call(client, action="add", **base)
    assert created["status"] == "created"
    updated = await _management_call(
        client, action="add", valid_from="2026-10-01T00:00:00Z", **base
    )
    assert updated["status"] == "updated"
    assert updated["memory"]["memory_id"] == created["memory"]["memory_id"]
    assert updated["memory"]["revision"] != created["memory"]["revision"]
    memory = await async_get_memory(
        hass, entry.entry_id, _conversation_subentry(entry).subentry_id
    )
    save = AsyncMock(wraps=memory._storage.async_save)
    monkeypatch.setattr(memory._storage, "async_save", save)
    duplicate = await _management_call(
        client, action="add", valid_from="2026-10-01T00:00:00Z", **base
    )
    assert (
        duplicate["status"] == "duplicate" and duplicate["memory"] == updated["memory"]
    )
    noop = await _management_call(
        client,
        action="update",
        memory_id=updated["memory"]["memory_id"],
        expected_revision=updated["memory"]["revision"],
        **base,
    )
    assert noop["status"] == "updated" and noop["memory"] == updated["memory"]
    conflict = await _management_response(
        client, action="add", valid_from="2026-10-02T00:00:00Z", **base
    )
    assert not conflict["success"]
    assert "valid_from; no changes made" in conflict["error"]["message"]
    assert "Update" in conflict["error"]["message"]
    save.assert_not_called()
    await _fresh_reload(hass, entry)
    listed = await _management_call(
        client, entry=entry, section="memories", action="list"
    )
    assert listed["memories"] == [updated["memory"]]
