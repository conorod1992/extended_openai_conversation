"""Stable coverage for native service ownership helper branches."""

from __future__ import annotations

import asyncio
from functools import partial
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses import ha_actions
from custom_components.extended_openai_conversation_responses.functions import native


def _registered(target):
    return SimpleNamespace(job=SimpleNamespace(target=target))


def _services(hass, target):
    hass.services.async_services_for_domain.return_value = {
        "turn_on": _registered(target)
    }


def test_service_participants_filters_mapping_entities(hass) -> None:
    entities = {"light.one": object(), "light.two": object()}
    target = partial(ha_actions.service_helpers.entity_service_call, object(), entities)
    _services(hass, target)

    assert native._service_participants(
        hass,
        "light",
        "turn_on",
        {"light.one", "light.missing"},
    ) == {"light.one"}


def test_service_participants_accepts_callable_entity_mapping(hass) -> None:
    entities = {"switch.one": object()}
    target = partial(
        ha_actions.service_helpers.entity_service_call,
        object(),
        lambda: entities,
    )
    _services(hass, target)

    assert native._service_participants(
        hass,
        "switch",
        "turn_on",
        {"switch.one", "switch.two"},
    ) == {"switch.one"}


def test_service_participants_filters_legacy_platform_collection(hass) -> None:
    platforms = [
        SimpleNamespace(entities={"light.one": object()}),
        SimpleNamespace(entities={"light.two": object()}),
    ]
    target = partial(ha_actions.service_helpers.entity_service_call, object(), platforms)
    _services(hass, target)

    assert native._service_participants(
        hass,
        "light",
        "turn_on",
        {"light.two", "light.three"},
    ) == {"light.two"}


@pytest.mark.parametrize(
    "registered",
    [
        None,
        SimpleNamespace(job=SimpleNamespace(target=lambda: None)),
        _registered(partial(ha_actions.service_helpers.entity_service_call, object())),
        _registered(
            partial(
                ha_actions.service_helpers.entity_service_call,
                object(),
                object(),
            )
        ),
    ],
)
def test_service_participants_returns_none_for_non_entity_service_shapes(
    hass, registered
) -> None:
    hass.services.async_services_for_domain.return_value = (
        {} if registered is None else {"turn_on": registered}
    )

    assert native._service_participants(hass, "light", "turn_on", {"light.one"}) is None


@pytest.mark.asyncio
async def test_settle_automation_update_returns_native_result() -> None:
    async def operation():
        await asyncio.sleep(0)
        return "saved"

    assert await native._async_settle_automation_update(operation()) == "saved"


@pytest.mark.asyncio
async def test_settle_automation_update_surfaces_native_failure() -> None:
    async def operation():
        await asyncio.sleep(0)
        raise OSError("reload failed")

    with pytest.raises(OSError, match="reload failed"):
        await native._async_settle_automation_update(operation())


@pytest.mark.asyncio
async def test_settle_automation_update_finishes_owned_operation_after_cancellation() -> (
    None
):
    started = asyncio.Event()
    finish = asyncio.Event()

    async def operation():
        started.set()
        await finish.wait()
        return "saved"

    owner = asyncio.create_task(native._async_settle_automation_update(operation()))
    await started.wait()
    owner.cancel()
    # Cancellation is deferred until the write has settled so the file and HA
    # reload cannot be left in an unknown half-completed state.
    await asyncio.sleep(0)
    owner.cancel()
    await asyncio.sleep(0)
    finish.set()

    with pytest.raises(asyncio.CancelledError):
        await owner


def test_exposed_entity_ids_ignores_non_string_ids() -> None:
    assert native._exposed_entity_ids(
        [
            {"entity_id": "light.one"},
            {"entity_id": 123},
            {},
        ]
    ) == {"light.one"}


def test_energy_entity_ids_recurses_only_entity_like_values() -> None:
    value = {
        "energy_sources": [
            {
                "stat_energy_from": "sensor.grid_import",
                "stat_energy_to": "sensor.grid_export",
                "price": "not-an-entity",
            },
            {
                "nested": {
                    "entity_id": "sensor.solar",
                }
            },
        ]
    }

    result = native._energy_entity_ids(value)

    assert "sensor.grid_import" in result
    assert "sensor.grid_export" in result
    assert "sensor.solar" in result
