"""Native target preparation and execution must agree, including saved jobs."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import ha_actions
from custom_components.extended_openai_conversation_responses.entity import (
    ExtendedOpenAIBaseLLMEntity,
)
from custom_components.extended_openai_conversation_responses.execution_arguments import (
    SERVICE_TARGET_KEYS,
    execution_identity,
    prepare_service_arguments,
)
from custom_components.extended_openai_conversation_responses.functions.native import (
    NativeFunction,
)
from homeassistant.exceptions import HomeAssistantError
from tests.test_delayed_tools import (
    DelayedToolCall,
    DelayedToolManager,
    _record,
    _valid_tool,
    tool_definition_fingerprint,
)


@pytest.mark.parametrize("key", SERVICE_TARGET_KEYS)
@pytest.mark.parametrize("empty", [None, [], "", " , ", ["", " , "]])
@pytest.mark.parametrize("batch", [False, True])
async def test_empty_only_selection_cannot_dispatch(hass, key, empty, batch):
    arguments = {"domain": "test", "service": "probe", "service_data": {key: empty}}
    native = NativeFunction()
    with pytest.raises(HomeAssistantError):
        if batch:
            await native.execute_service(hass, {}, {"list": [arguments]}, None, [])
        else:
            await native.execute_service_single(hass, {}, arguments, None, [])
    hass.services.async_call.assert_not_awaited()


@pytest.mark.parametrize("key", SERVICE_TARGET_KEYS)
@pytest.mark.parametrize("bad", [1, False, {}, [None], [1], [["light.kitchen"]]])
async def test_malformed_selector_cannot_hide_behind_valid_alternative(hass, key, bad):
    alternative = "area_id" if key == "entity_id" else "entity_id"
    with pytest.raises(HomeAssistantError, match="string or list of strings"):
        await NativeFunction().execute_service_single(
            hass,
            {},
            {
                "domain": "test",
                "service": "probe",
                "service_data": {
                    key: bad,
                    alternative: "light.kitchen",
                },
            },
            None,
            [{"entity_id": "light.kitchen"}],
        )
    hass.services.async_call.assert_not_awaited()


@pytest.mark.parametrize("key", SERVICE_TARGET_KEYS)
async def test_valid_selector_survives_other_empty_selectors(hass, monkeypatch, key):
    selected = {name: [] for name in SERVICE_TARGET_KEYS}
    selected[key] = "light.kitchen" if key == "entity_id" else "kitchen"
    monkeypatch.setattr(
        ha_actions.target_helpers,
        "async_extract_referenced_entity_ids",
        lambda _hass, _selection: SimpleNamespace(
            referenced={"light.kitchen"}, indirectly_referenced=set()
        ),
    )
    result = await NativeFunction().execute_service_single(
        hass,
        {},
        {"domain": "test", "service": "probe", "service_data": selected},
        None,
        [{"entity_id": "light.kitchen"}],
    )
    assert result["success"]
    hass.services.async_call.assert_awaited_once()


def test_preparation_preserves_precedence_and_matches_replay():
    arguments = {
        "domain": "LIGHT",
        "service": "TURN_ON",
        "entity_id": "light.ignored",
        "data": {"entity_id": "light.also_ignored"},
        "service_data": {
            "entity_id": [],
            "area_id": " kitchen, hall ",
            "label_id": ["day, evening", " "],
        },
    }
    original = deepcopy(arguments)
    prepared = prepare_service_arguments(arguments)
    assert arguments == original
    assert prepared["service_data"] == {
        "entity_id": [],
        "area_id": " kitchen, hall ",
        "label_id": ["day, evening", " "],
    }
    config = {"type": "native", "name": "execute_service_single"}
    assert execution_identity(config, arguments) == execution_identity(config, prepared)
    # Arbitrary handlers distinguish indirect scalars/lists and raw list items.
    # Do not collapse these observable differences in failed-continuation replay.
    for key in SERVICE_TARGET_KEYS:
        scalar = {
            "domain": "test",
            "service": "probe",
            "service_data": {key: "kitchen"},
        }
        sequence = {
            "domain": "test",
            "service": "probe",
            "service_data": {key: ["kitchen"]},
        }
        assert (
            execution_identity(config, scalar) == execution_identity(config, sequence)
        ) is (key == "entity_id")
    assert execution_identity(
        config, {"domain": "light", "service": "turn_on", "entity_id": "light.kitchen"}
    ) == execution_identity(
        config,
        {
            "domain": "light",
            "service": "turn_on",
            "data": {"entity_id": ["light.kitchen"]},
        },
    )


@pytest.mark.parametrize("key", SERVICE_TARGET_KEYS)
async def test_existing_delayed_record_reaches_current_native_guard(
    hass, monkeypatch, key
):
    """An already stored job needs no migration to encounter the execution guard."""
    tool = _valid_tool()
    saved = _record().as_dict()
    saved["arguments"] = {
        "domain": "test",
        "service": "probe",
        "service_data": {key: " , "},
    }
    saved["user_id"] = None
    saved["definition_fingerprint"] = tool_definition_fingerprint(tool)
    record = DelayedToolCall.from_dict(saved)
    manager = DelayedToolManager(hass)
    manager._records = {record.call_id: record}
    manager._store = SimpleNamespace(async_save=AsyncMock())
    hass.config_entries.async_get_entry.return_value = SimpleNamespace(
        disabled_by=None,
        subentries={"agent": SimpleNamespace(subentry_type="conversation", data={})},
    )
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.delayed_tools.configured_function_tools_from_data",
        lambda _: [tool],
    )
    guarded = []
    entity = SimpleNamespace(hass=hass, entity_id="conversation.probe")

    async def execute(_tool, call, context, _exposed):
        with pytest.raises(HomeAssistantError):
            await ExtendedOpenAIBaseLLMEntity._execute_function_tool(
                entity, _tool, call, context, _exposed
            )
        guarded.append(True)

    monkeypatch.setattr(
        manager,
        "_resolve_agent",
        lambda *_: SimpleNamespace(_execute_function_tool=execute),
    )
    assert await manager._async_execute_due(record.call_id) is False
    assert guarded == [True]
    hass.services.async_call.assert_not_awaited()
