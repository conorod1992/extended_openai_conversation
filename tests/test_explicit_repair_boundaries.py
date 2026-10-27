"""Repair inspection cannot change strict validation in related async work."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import (
    agent_config,
    management_function_quarantine as inspection,
    management_request_preview as preview,
    management_ui as ui,
)
from homeassistant.exceptions import HomeAssistantError


@pytest.mark.parametrize("outcome", ["success", "failure", "cancel"])
async def test_suspended_inspection_cannot_authorize_strict_preview_or_child(
    hass, management_message, monkeypatch, outcome
):
    source = {
        "functions": [
            {
                "spec": {"name": "usable", "parameters": {"type": "object"}},
                "function": {"type": "template", "value_template": "{{ 42 }}"},
            },
            {
                "spec": {"name": "damaged", "parameters": {"type": "object"}},
                "function": {"type": "native", "name": "future.unavailable"},
                "future_field": {"retain": [None, 7]},
            },
        ],
        "function_groups": [],
    }
    original = deepcopy(source)
    entered, finish = asyncio.Event(), asyncio.Event()
    after_strict_validation = AsyncMock(
        side_effect=AssertionError("invalid configuration reached preview runtime")
    )
    monkeypatch.setattr(preview, "async_get_guest_mode", after_strict_validation)

    async def strict_preview():
        # A copied task context must not change the default strict contract.
        with pytest.raises(agent_config.AgentConfigError):
            await preview.async_preview_effective_request(
                hass,
                SimpleNamespace(entry_id="other-provider"),
                SimpleNamespace(subentry_id="other-agent"),
                source,
                "other-user",
            )

    async def repair_handler(request):
        assert [
            tool["spec"]["name"] for tool in inspection.inspect_function_tools(source)
        ] == ["usable"]
        await asyncio.create_task(strict_preview())
        entered.set()
        await finish.wait()
        if outcome == "failure":
            raise HomeAssistantError("inspection failed")
        return {"inspected": True}

    monkeypatch.setattr(ui, "_MANAGEMENT_SECTION_HANDLERS", {"tools": repair_handler})
    pending = asyncio.create_task(
        ui.async_management_command(
            hass, "admin", True, management_message("tools", "list")
        )
    )
    entered_waiter = asyncio.create_task(entered.wait())
    try:
        async with asyncio.timeout(10):
            await asyncio.wait(
                {pending, entered_waiter}, return_when=asyncio.FIRST_COMPLETED
            )
        if pending.done():
            await (
                pending
            )  # Surface a failed child assertion before waiting for admission.
        assert entered.is_set()
        # Concurrent work for another user remains strict while inspection owns
        # its maintenance lease, including before failure/cancellation cleanup.
        await strict_preview()
        with pytest.raises(agent_config.AgentConfigError):
            agent_config.configured_function_tools_from_data(source)
        if outcome == "cancel":
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
        else:
            finish.set()
            if outcome == "failure":
                with pytest.raises(HomeAssistantError, match="inspection failed"):
                    await pending
            else:
                assert await pending == {"inspected": True}
        await strict_preview()
        assert source == original
        after_strict_validation.assert_not_awaited()
    finally:
        finish.set()
        if not pending.done():
            pending.cancel()
        if not entered_waiter.done():
            entered_waiter.cancel()
        await asyncio.gather(pending, entered_waiter, return_exceptions=True)
