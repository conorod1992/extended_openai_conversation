"""Operation classification must preserve public admission and ownership policy."""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.extended_openai_conversation_responses import (
    agent_maintenance,
    management_ui as ui,
)
from custom_components.extended_openai_conversation_responses.management_operations import (
    TOOL_MUTATIONS,
    is_tool_mutation,
)
from homeassistant.exceptions import HomeAssistantError


@pytest.mark.parametrize(
    "section", ["tools", "function_repair", "configuration", None, []]
)
@pytest.mark.parametrize("action", [None, True, 1, [], {}, {"save"}])
def test_unvalidated_identifiers_do_not_select_a_tool_mutation(section, action):
    assert is_tool_mutation(section, action) is False


@pytest.mark.parametrize(
    "action",
    ["list", "serialize", "validate", "ha_catalog", "save_one", "future_operation"],
)
def test_reads_and_other_families_do_not_enter_tool_mutation_policy(action):
    assert not is_tool_mutation("tools", action)
    assert not is_tool_mutation("function_repair", "save")
    assert not is_tool_mutation("configuration", "save")


@pytest.mark.parametrize("action", sorted(TOOL_MUTATIONS))
async def test_tool_mutation_stale_revision_rejects_before_handler(
    hass, management_message, monkeypatch, action
):
    handler = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(ui, "_MANAGEMENT_SECTION_HANDLERS", {"tools": handler})
    with pytest.raises(HomeAssistantError, match="changed in another tab"):
        await ui.async_management_command(
            hass, "admin", True, management_message("tools", action, revision="stale")
        )
    handler.assert_not_awaited()
    hass.config_entries.async_update_subentry.assert_not_called()


@pytest.mark.parametrize("action", sorted(TOOL_MUTATIONS))
async def test_tool_mutation_legacy_revision_remains_optional(
    hass, management_message, monkeypatch, action
):
    handler = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(ui, "_MANAGEMENT_SECTION_HANDLERS", {"tools": handler})
    message = management_message("tools", action)
    message.pop("revision", None)
    assert await ui.async_management_command(hass, "admin", True, message) == {
        "ok": True
    }
    handler.assert_awaited_once()


@pytest.mark.parametrize("action", sorted(TOOL_MUTATIONS))
async def test_tool_permission_precedes_revision_but_follows_agent_selection(
    hass, management_agent, management_message, monkeypatch, action
):
    entry, subentry = management_agent
    select = Mock(wraps=ui.entry_and_agent)
    monkeypatch.setattr(ui, "entry_and_agent", select)
    with pytest.raises(
        HomeAssistantError, match="Administrator permission is required"
    ):
        await ui.async_management_command(
            hass,
            "other-user",
            False,
            management_message("tools", action, revision=123, tool="invalid"),
        )
    select.assert_called_once_with(hass, entry.entry_id, subentry.subentry_id)
    hass.config_entries.async_update_subentry.assert_not_called()


@pytest.mark.parametrize("action", ["save", "save_group"])
async def test_tool_and_group_mutations_keep_shared_maintenance_until_cancelled(
    hass, management_message, monkeypatch, action
):
    gate = agent_maintenance.get_agent_maintenance_gate(hass, "entry-1", "agent-1")
    entered = asyncio.Event()
    finish = asyncio.Event()

    async def handler(request):
        assert gate._active_readers == 1
        entered.set()
        await finish.wait()
        return {"ok": True}

    monkeypatch.setattr(ui, "_MANAGEMENT_SECTION_HANDLERS", {"tools": handler})
    async with gate.exclusive():
        task = asyncio.create_task(
            ui.async_management_command(
                hass, "admin", True, management_message("tools", action)
            )
        )
        await asyncio.sleep(0)  # Yield once to exercise pending writer admission.
        assert not entered.is_set()
    try:
        async with asyncio.timeout(2):
            await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert gate._active_readers == 0
        async with gate.exclusive():
            assert gate._active_readers == 0
            assert gate.owns_exclusive()
    finally:
        finish.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
