"""Optional observers cannot reject committed state or starve siblings."""

import pytest
from custom_components.extended_openai_conversation_responses.guest_mode import (
    GuestModeManager,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    RequestRules,
)
from tests.test_request_rules import MemoryStore, local_rule


async def test_guest_listeners_cannot_reject_commits_or_starve_other_listeners(
    hass, caplog
):
    manager = GuestModeManager(hass, "entry", "agent")
    await manager.async_initialize()
    observed = []

    def fail():
        raise ValueError("PRIVATE-LISTENER-SENTINEL")

    manager.async_add_listener(fail)
    manager.async_add_listener(lambda: observed.append(manager.is_active()))
    assert (await manager.async_update_trusted(indefinite=True))["currently_active"]
    assert observed == [True]
    fresh = GuestModeManager(hass, "entry", "agent")
    await fresh.async_initialize()
    assert fresh.is_active()
    assert not (await manager.async_disable_trusted())["currently_active"]
    assert observed == [True, False]
    assert "PRIVATE-LISTENER-SENTINEL" not in caplog.text


async def test_rule_callback_failure_preserves_commit_and_next_revision(caplog):
    def fail():
        raise ValueError("PRIVATE-CALLBACK-SENTINEL")

    store = MemoryStore()
    manager = RequestRules(store, on_change=fail)
    await manager.async_initialize()
    created = await manager.async_create(local_rule("First"))
    fresh = RequestRules(store)
    await fresh.async_initialize()
    assert fresh.rule_snapshot(created["id"])["name"] == "First"
    await manager.async_update(
        created["id"],
        {**manager.rule_snapshot(created["id"]), "name": "Next"},
        expected_revision=manager.revision(),
    )
    fresh = RequestRules(store)
    await fresh.async_initialize()
    assert fresh.rule_snapshot(created["id"])["name"] == "Next"
    assert "PRIVATE-CALLBACK-SENTINEL" not in caplog.text
