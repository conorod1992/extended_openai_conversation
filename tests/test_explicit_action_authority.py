"""Missing authority cannot inherit the intentional userless execution policy."""

import asyncio
from contextvars import Context as TaskContext

import pytest

from custom_components.extended_openai_conversation_responses.ha_actions import (
    async_call_ha_action,
)
from custom_components.extended_openai_conversation_responses.ha_permissions import (
    async_require_control_permission,
    bind_active_ha_context,
    require_active_ha_context,
)
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError


@pytest.mark.parametrize("action", [False, True])
async def test_unbound_authority_is_rejected_before_effects(hass, action):
    operation = (
        async_call_ha_action(
            hass, "light", "turn_on", target={"entity_id": "light.test"}
        )
        if action
        else async_require_control_permission(hass, ["light.test"])
    )
    task = asyncio.create_task(operation, context=TaskContext())
    with pytest.raises(HomeAssistantError, match="authorization context is missing"):
        await task
    hass.services.async_call.assert_not_awaited()
    hass.auth.async_get_user.assert_not_awaited()


@pytest.mark.parametrize("context", [None, Context()])
async def test_deliberately_bound_userless_context_remains_supported(hass, context):
    with bind_active_ha_context(context):
        assert await async_require_control_permission(hass, ["light.test"]) is context
    hass.auth.async_get_user.assert_not_awaited()


async def test_nested_context_restores_missing_authority_after_exit(hass):
    async def check():
        with bind_active_ha_context(Context()):
            assert await async_require_control_permission(hass, []) is not None
        with pytest.raises(
            HomeAssistantError, match="authorization context is missing"
        ):
            require_active_ha_context()

    await asyncio.create_task(check(), context=TaskContext())
