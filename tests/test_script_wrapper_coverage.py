"""Small stable-coverage wins for Script Function proxy wrappers."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.extended_openai_conversation_responses.functions import script


def test_authorized_script_services_proxies_unknown_attributes() -> None:
    services = SimpleNamespace(example="value")
    hass = SimpleNamespace(services=services)
    wrapper = script._AuthorizedScriptServices(
        hass,
        SimpleNamespace(validate_entity_ids=Mock()),
        [],
    )

    assert wrapper.example == "value"


def test_authorized_script_hass_proxies_unknown_attributes() -> None:
    hass = SimpleNamespace(services=SimpleNamespace(), example="value")
    wrapper = script._AuthorizedScriptHass(
        hass,
        SimpleNamespace(validate_entity_ids=Mock()),
        [],
    )

    assert wrapper.example == "value"


@pytest.mark.asyncio
async def test_authorized_script_service_preserves_native_call_contract(
    monkeypatch,
) -> None:
    service_call = AsyncMock(return_value={"ok": True})
    hass = SimpleNamespace(services=SimpleNamespace(async_call=service_call))
    function = SimpleNamespace(validate_entity_ids=Mock())
    wrapper = script._AuthorizedScriptServices(
        hass,
        function,
        [{"entity_id": "light.kitchen"}],
    )
    authorize = AsyncMock(return_value={"light.kitchen"})
    monkeypatch.setattr(script.ha_actions, "async_authorize_ha_action", authorize)
    context = object()

    result = await wrapper.async_call(
        "light",
        "turn_on",
        {"brightness": 128},
        blocking=True,
        context=context,
        target={"entity_id": "light.kitchen"},
        return_response=True,
    )

    assert result == {"ok": True}
    authorize.assert_awaited_once_with(
        hass,
        "light",
        "turn_on",
        data={"brightness": 128},
        target={"entity_id": "light.kitchen"},
        context=context,
    )
    function.validate_entity_ids.assert_called_once_with(
        hass,
        ["light.kitchen"],
        [{"entity_id": "light.kitchen"}],
        require_available=True,
    )
    service_call.assert_awaited_once_with(
        "light",
        "turn_on",
        {"brightness": 128},
        blocking=True,
        context=context,
        target={"entity_id": "light.kitchen"},
        return_response=True,
    )
