"""Closing regression coverage for the 6.4.0 hardening sweep."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from homeassistant.auth.permissions.const import POLICY_CONTROL
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError

from custom_components.extended_openai_conversation_responses.built_in_functions import (
    built_in_function_catalog,
)
from custom_components.extended_openai_conversation_responses.exceptions import (
    EntityNotExposed,
)
from custom_components.extended_openai_conversation_responses.functions import (
    NativeFunction,
)
from custom_components.extended_openai_conversation_responses.ha_permissions import (
    async_require_control_permission,
)


async def test_restricted_ha_user_cannot_authorize_unresolved_action(hass) -> None:
    """An empty target cannot turn a restricted user's action into an all-entity call."""
    context = Context(user_id="restricted")
    user = MagicMock(is_active=True, is_admin=False)
    user.permissions.access_all_entities.return_value = False
    hass.auth.async_get_user = AsyncMock(return_value=user)

    with pytest.raises(HomeAssistantError, match="without a resolvable entity target"):
        await async_require_control_permission(hass, [], context=context)

    user.permissions.access_all_entities.assert_called_once_with(POLICY_CONTROL)


async def test_full_control_user_can_authorize_non_entity_action(hass) -> None:
    """The fail-closed target rule must not block users with global CONTROL access."""
    context = Context(user_id="full-control")
    user = MagicMock(is_active=True, is_admin=False)
    user.permissions.access_all_entities.return_value = True
    hass.auth.async_get_user = AsyncMock(return_value=user)

    assert await async_require_control_permission(hass, [], context=context) is context


async def test_statistics_require_an_explicit_non_empty_selection(
    hass, exposed_entities
) -> None:
    """Missing IDs must never fall through to Recorder's all-statistics behavior."""
    function = NativeFunction()
    with pytest.raises(HomeAssistantError, match="non-empty list"):
        await function.get_statistics(
            hass,
            {},
            {
                "start_time": "2026-08-01T00:00:00Z",
                "end_time": "2026-08-02T00:00:00Z",
            },
            None,
            exposed_entities,
        )


async def test_statistics_reject_unexposed_entity_backed_ids(
    hass, exposed_entities
) -> None:
    """Long-term statistics for entity IDs inherit the Assist exposure boundary."""
    function = NativeFunction()
    with pytest.raises(EntityNotExposed, match="sensor.secret"):
        await function.get_statistics(
            hass,
            {},
            {
                "statistic_ids": ["sensor.secret"],
                "start_time": "2026-08-01T00:00:00Z",
                "end_time": "2026-08-02T00:00:00Z",
            },
            None,
            exposed_entities,
        )


async def test_statistics_keep_external_statistic_ids_supported(
    hass, exposed_entities, monkeypatch
) -> None:
    """Recorder-native '<domain>:<statistic>' IDs are not mistaken for HA entities."""
    function = NativeFunction()
    recorder_instance = MagicMock()
    recorder_instance.async_add_executor_job = AsyncMock(
        return_value={"solaredge:energy": []}
    )
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.functions.native.recorder.get_instance",
        lambda _hass: recorder_instance,
    )

    result = await function.get_statistics(
        hass,
        {},
        {
            "statistic_ids": ["solaredge:energy"],
            "start_time": "2026-08-01T00:00:00Z",
            "end_time": "2026-08-02T00:00:00Z",
            "period": "hour",
        },
        None,
        exposed_entities,
    )

    assert result == {"solaredge:energy": []}
    assert recorder_instance.async_add_executor_job.await_args.args[4] == {
        "solaredge:energy"
    }


async def test_statistics_selection_has_a_runtime_upper_bound(
    hass, exposed_entities
) -> None:
    """Custom tool schemas cannot bypass the built-in statistics selection cap."""
    function = NativeFunction()
    with pytest.raises(HomeAssistantError, match="at most 100"):
        await function.get_statistics(
            hass,
            {},
            {
                "statistic_ids": [f"sensor.item_{index}" for index in range(101)],
                "start_time": "2026-08-01T00:00:00Z",
                "end_time": "2026-08-02T00:00:00Z",
            },
            None,
            exposed_entities,
        )


async def test_energy_configuration_rejects_hidden_entity_ids(
    hass, exposed_entities, monkeypatch
) -> None:
    """Energy preferences cannot disclose entity IDs hidden from Assist."""
    function = NativeFunction()
    manager = SimpleNamespace(
        data={
            "energy_sources": [
                {
                    "type": "grid",
                    "stat_energy_from": "sensor.secret_energy",
                    "stat_cost": "utility:cost",
                }
            ],
            "device_consumption": [],
        }
    )
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.functions.native.energy.async_get_manager",
        AsyncMock(return_value=manager),
    )

    with pytest.raises(HomeAssistantError, match="not exposed to Assist"):
        await function.get_energy(hass, {}, {}, None, exposed_entities)


async def test_energy_configuration_keeps_external_statistics_supported(
    hass, exposed_entities, monkeypatch
) -> None:
    """External stats and entity-shaped display names do not require exposure."""
    function = NativeFunction()
    data = {
        "energy_sources": [
            {
                "type": "solar",
                "name": "grid.main",
                "stat_energy_from": "solaredge:production",
                "config_entry_solar_forecast": ["opaque-config-entry-id"],
            }
        ],
        "device_consumption": [],
    }
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.functions.native.energy.async_get_manager",
        AsyncMock(return_value=SimpleNamespace(data=data)),
    )

    assert await function.get_energy(hass, {}, {}, None, exposed_entities) == data


def test_builtin_statistics_schema_requires_bounded_ids() -> None:
    """The model-facing preset should make the safe runtime contract explicit."""
    preset = next(
        item
        for item in built_in_function_catalog()
        if item["implementation"] == "get_statistics"
    )
    parameters = preset["tool"]["spec"]["parameters"]
    statistic_ids = parameters["properties"]["statistic_ids"]

    assert "statistic_ids" in parameters["required"]
    assert statistic_ids["minItems"] == 1
    assert statistic_ids["maxItems"] == 100
