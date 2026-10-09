"""Real HA assistant removal must release owned managers without harming siblings."""
from tests.resource_lifecycle_ledger import capture_owned, assert_owned_removed
from tests_real_ha.test_live_subentry_removal import (
    _entry, _setup_entry, _subentry_by_title,
)


async def test_remove_one_assistant_releases_owned_managers_preserves_sibling(hass):
    entry = _entry()
    await _setup_entry(hass, entry)
    removed = _subentry_by_title(entry, "Conversation A")
    sibling = _subentry_by_title(entry, "Conversation B")
    before = capture_owned(hass, entry.entry_id, removed.subentry_id)
    sibling_before = capture_owned(hass, entry.entry_id, sibling.subentry_id)
    assert before.manager_keys, "Lifecycle fixture must observe actual owned resources"
    assert sibling_before.manager_keys, "Sibling control must have its own resources"

    assert hass.config_entries.async_remove_subentry(entry, removed.subentry_id)
    await hass.async_block_till_done()

    after = capture_owned(hass, entry.entry_id, removed.subentry_id)
    assert_owned_removed(before, after)
    sibling_after = capture_owned(hass, entry.entry_id, sibling.subentry_id)
    assert sibling_after.manager_keys, "Deleting one assistant stopped its sibling"
    assert sibling_after.manager_keys == sibling_before.manager_keys

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert_owned_removed(
        sibling_before, capture_owned(hass, entry.entry_id, sibling.subentry_id)
    )
