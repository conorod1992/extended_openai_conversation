"""API discovery uses Core's public authorization gate on released and dev HA."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses.ha_llm_tools import (
    async_discover,
)
from homeassistant.components import llm as llm_component
from homeassistant.exceptions import Unauthorized
from homeassistant.helpers import llm
from tests.test_ha_llm_tools import TestAPI, context, register


async def test_discovery_acquires_api_through_public_entry_point(hass, monkeypatch):
    api = register(hass, TestAPI(hass))
    acquire = AsyncMock(wraps=llm.async_get_api)
    monkeypatch.setattr(llm, "async_get_api", acquire)
    current = context()
    snapshot = await async_discover(hass, current)
    acquire.assert_awaited_once_with(hass, api.id, current)
    live = next(iter(snapshot.tools.values()))
    assert live.instance.llm_context is current
    await live.async_call(llm.ToolInput(tool_name="echo", tool_args={"value": "ok"}))
    assert len(api.tools[0].calls) == 1


async def test_public_authorization_denial_never_calls_api_implementation(
    hass, monkeypatch
):
    """Released HA also delegates policy to Core rather than the internal hook."""
    api = register(hass, TestAPI(hass))
    monkeypatch.setattr(llm, "async_get_api", AsyncMock(side_effect=Unauthorized))
    snapshot = await async_discover(hass, context("member"))
    assert not snapshot.tools
    assert snapshot.unavailable_sources == [api.id]
    assert api.contexts == []
    assert api.tools[0].calls == []


async def test_platform_attribution_cannot_bypass_an_api_authorization_gate(
    hass, monkeypatch
):
    api = register(hass, llm_component.AssistAPI(hass))
    monkeypatch.setattr(type(api), "requires_admin", True, raising=False)
    acquire = AsyncMock(side_effect=Unauthorized)
    monkeypatch.setattr(llm, "async_get_api", acquire)
    platforms = AsyncMock(return_value={})
    hass.data[llm_component.DATA_PLATFORMS] = SimpleNamespace(
        async_get_platforms=platforms
    )
    current = context("member")
    snapshot = await async_discover(hass, current)
    acquire.assert_awaited_once_with(hass, api.id, current)
    assert not snapshot.tools
    assert snapshot.unavailable_sources == [api.id]
    platforms.assert_not_awaited()


@pytest.mark.skipif(
    not hasattr(llm.API, "requires_admin"),
    reason="HA release predates admin-only LLM APIs",
)
@pytest.mark.parametrize(
    ("user", "admin", "allowed"),
    [("admin", True, True), ("member", False, False), (None, False, False)],
)
async def test_native_admin_api_gate_controls_discovery_and_execution(
    hass, user, admin, allowed
):
    api = register(hass, TestAPI(hass))
    api.requires_admin = True
    hass.auth.async_get_user = AsyncMock(return_value=SimpleNamespace(is_admin=admin))
    snapshot = await async_discover(hass, context(user))
    assert bool(snapshot.tools) is allowed
    if allowed:
        live = next(iter(snapshot.tools.values()))
        await live.async_call(
            llm.ToolInput(tool_name="echo", tool_args={"value": "ok"})
        )
        assert len(api.tools[0].calls) == 1
    else:
        assert snapshot.unavailable_sources == [api.id]
        assert api.contexts == []
        assert api.tools[0].calls == []


@pytest.mark.parametrize("user", ["member", None])
async def test_unrestricted_api_still_available_without_admin(hass, user):
    api = register(hass, TestAPI(hass))
    current = context(user)
    snapshot = await async_discover(hass, current)
    assert len(snapshot.tools) == 1
    assert api.contexts == [current]
