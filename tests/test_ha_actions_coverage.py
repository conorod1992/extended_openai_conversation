"""Residual branch coverage for shared Home Assistant action helpers."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from homeassistant.core import Context, State
from homeassistant.exceptions import ServiceNotFound

from custom_components.extended_openai_conversation_responses import ha_actions


def _state(entity_id: str, value: str, **attributes: Any) -> State:
    return State(entity_id, value, attributes)


@pytest.mark.asyncio
async def test_unchecked_action_rejects_missing_service() -> None:
    hass = SimpleNamespace(
        services=SimpleNamespace(
            has_service=lambda _domain, _service: False,
            async_call=AsyncMock(),
        )
    )

    with pytest.raises(ServiceNotFound):
        await ha_actions._async_call_ha_action_unchecked(
            hass, "light", "missing", data={"brightness": 10}
        )

    hass.services.async_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_actions_runs_in_order_with_blocking_and_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    context = Context(user_id="user-1")

    async def fake_call(
        hass: object,
        domain: str,
        service: str,
        **kwargs: Any,
    ) -> dict[str, dict[str, Any]]:
        calls.append({"domain": domain, "service": service, **kwargs})
        return {f"{domain}.entity": {"state": service}}

    monkeypatch.setattr(ha_actions, "async_call_ha_action", fake_call)

    result = await ha_actions.async_execute_ha_actions(
        SimpleNamespace(),
        [
            {
                "domain": "light",
                "service": "turn_on",
                "data": {"brightness": 50},
                "target": {"entity_id": "light.kitchen"},
            },
            {"domain": "switch", "service": "turn_off"},
        ],
        context=context,
    )

    assert result == [
        {"light.entity": {"state": "turn_on"}},
        {"switch.entity": {"state": "turn_off"}},
    ]
    assert calls == [
        {
            "domain": "light",
            "service": "turn_on",
            "data": {"brightness": 50},
            "target": {"entity_id": "light.kitchen"},
            "blocking": True,
            "context": context,
        },
        {
            "domain": "switch",
            "service": "turn_off",
            "data": {},
            "target": {},
            "blocking": True,
            "context": context,
        },
    ]


def test_unknown_stateful_domain_uses_state_only_fallback() -> None:
    assert ha_actions.serialize_reversible_state(
        _state("vacuum.downstairs", "cleaning", private_detail="ignored")
    ) == {"state": "cleaning"}


def test_capture_returns_empty_when_hass_has_no_state_machine() -> None:
    assert (
        ha_actions._capture_previous_state(
            SimpleNamespace(),
            "light",
            "turn_on",
            None,
            {"entity_id": "light.kitchen"},
        )
        == {}
    )


def test_capture_skips_missing_or_non_state_entities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = SimpleNamespace(states=SimpleNamespace(get=lambda _entity_id: object()))
    monkeypatch.setattr(
        ha_actions,
        "_resolve_target_entity_ids",
        lambda *_args: {"light.kitchen"},
    )

    assert (
        ha_actions._capture_previous_state(
            hass,
            "light",
            "turn_on",
            None,
            {"entity_id": "light.kitchen"},
        )
        == {}
    )


def test_homeassistant_capture_skips_non_reversible_entity_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _state("button.restart", "unknown")
    hass = SimpleNamespace(states=SimpleNamespace(get=lambda _entity_id: state))
    monkeypatch.setattr(
        ha_actions,
        "_resolve_target_entity_ids",
        lambda *_args: {"button.restart"},
    )

    assert (
        ha_actions._capture_previous_state(
            hass,
            "homeassistant",
            "turn_on",
            None,
            {"entity_id": "button.restart"},
        )
        == {}
    )


def test_resolve_target_ids_short_circuits_empty_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolver = MagicMock()
    monkeypatch.setattr(
        ha_actions.target_helpers,
        "async_extract_referenced_entity_ids",
        resolver,
    )

    assert ha_actions._resolve_target_entity_ids(SimpleNamespace(), {}, {}) == set()
    resolver.assert_not_called()


def test_target_selection_merges_scalar_and_list_values() -> None:
    assert ha_actions._target_selection(
        {
            "entity_id": "light.one",
            "area_id": ["kitchen", "hall"],
            "device_id": None,
        },
        {
            "entity_id": ["light.two", "light.three"],
            "area_id": "office",
            "label_id": "important",
        },
    ) == {
        "entity_id": ["light.one", "light.two", "light.three"],
        "area_id": ["kitchen", "hall", "office"],
        "label_id": ["important"],
    }
