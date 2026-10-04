"""Deleted-user memory editing through authenticated HA WebSocket commands."""

from pytest_homeassistant_custom_component.common import CLIENT_ID, MockUser

from custom_components.extended_openai_conversation_responses.scope import (
    SHARED_HOUSEHOLD_SCOPE_ID,
)
from tests_real_ha.test_management_backend_acceptance import (
    _admin_client,
    _entry,
    _fresh_reload,
    _management_call,
    _management_response,
    _setup_entry,
)


async def test_admin_edit_preserves_deleted_owner_without_permitting_new_assignments(
    hass, hass_ws_client
):
    entry = _entry("Deleted memory owner")
    await _setup_entry(hass, entry)
    admin = await _admin_client(hass, hass_ws_client)
    owner = MockUser(id="memory-owner-to-delete", is_owner=False)
    owner.add_to_hass(hass)
    scope = f"user:{owner.id}"
    added = await _management_call(
        admin,
        entry=entry,
        section="memories",
        action="add",
        scope_id=scope,
        target_scope_id=scope,
        content="Original memory",
    )
    memory_id = added["memory"]["memory_id"]
    await hass.auth.async_remove_user(owner)
    assert await hass.auth.async_get_user(owner.id) is None

    updated = await _management_call(
        admin,
        entry=entry,
        section="memories",
        action="update",
        scope_id=scope,
        target_scope_id=scope,
        memory_id=memory_id,
        content="Corrected memory",
        category="home",
        expected_revision=added["memory"]["revision"],
    )
    assert updated["scope_id"] == scope
    assert updated["memory"]["content"] == "Corrected memory"
    await _fresh_reload(hass, entry)
    before = await _management_call(
        admin,
        entry=entry,
        section="memories",
        action="list",
        scope_id=scope,
    )
    assert len(before["memories"]) == 1
    assert before["memories"][0]["memory_id"] == memory_id
    assert before["memories"][0]["content"] == "Corrected memory"
    assert before["memories"][0]["category"] == "home"

    # Creating or moving ownership to a deleted user remains forbidden.
    failed_add = await _management_response(
        admin,
        entry=entry,
        section="memories",
        action="add",
        scope_id=scope,
        target_scope_id=scope,
        content="New orphan",
    )
    assert not failed_add["success"]
    assert "no longer exists" in failed_add["error"]["message"]
    shared = await _management_call(
        admin,
        entry=entry,
        section="memories",
        action="add",
        scope_id=SHARED_HOUSEHOLD_SCOPE_ID,
        content="Shared memory",
    )
    failed_move = await _management_response(
        admin,
        entry=entry,
        section="memories",
        action="update",
        scope_id=SHARED_HOUSEHOLD_SCOPE_ID,
        target_scope_id=scope,
        memory_id=shared["memory"]["memory_id"],
        content="Moved orphan",
        expected_revision=shared["memory"]["revision"],
    )
    assert not failed_move["success"]
    assert "no longer exists" in failed_move["error"]["message"]

    other = MockUser(id="other-memory-user", is_owner=False)
    other.add_to_hass(hass)
    token = await hass.auth.async_create_refresh_token(other, CLIENT_ID)
    restricted = await hass_ws_client(hass, hass.auth.async_create_access_token(token))
    denied = await _management_response(
        restricted,
        entry=entry,
        section="memories",
        action="update",
        scope_id=scope,
        target_scope_id=scope,
        memory_id=memory_id,
        content="Unauthorized edit",
        expected_revision=before["memories"][0]["revision"],
    )
    assert not denied["success"]
    assert "not available to the current user" in denied["error"]["message"]
    after = await _management_call(
        admin,
        entry=entry,
        section="memories",
        action="list",
        scope_id=scope,
    )
    assert after["memories"] == before["memories"]
    shared_after = await _management_call(
        admin,
        entry=entry,
        section="memories",
        action="list",
        scope_id=SHARED_HOUSEHOLD_SCOPE_ID,
    )
    assert shared_after["memories"][0]["content"] == "Shared memory"
