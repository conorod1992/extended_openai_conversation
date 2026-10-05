"""Small deterministic tests that close scattered stable-CI coverage gaps."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from custom_components.extended_openai_conversation_responses import (
    function_execution,
    ha_permissions,
    model_tool_results,
    safety_hardening,
    skill_availability,
)
from custom_components.extended_openai_conversation_responses.const import (
    FUNCTION_GROUP_LOADING_ON_DEMAND,
)
from custom_components.extended_openai_conversation_responses.functions.template import (
    TemplateFunction,
)
from homeassistant.exceptions import HomeAssistantError


def test_matches_json_type_covers_object_fallback() -> None:
    assert function_execution._matches_json_type({"a": 1}, "object")
    assert not function_execution._matches_json_type(["a"], "object")


def test_permission_compat_filter_checks_each_entity() -> None:
    check = Mock(side_effect=lambda entity_id, policy: entity_id.endswith("allowed"))
    user = SimpleNamespace(
        permissions=SimpleNamespace(check_entity=check),
    )

    result = ha_permissions._filter_entity_ids_compat(
        user,
        ["light.allowed", "light.denied"],
        "read",
    )

    assert result == ["light.allowed"]
    assert check.call_count == 2


@pytest.mark.asyncio
async def test_template_function_authorizes_list_entity_ids(
    hass, exposed_entities
) -> None:
    function = TemplateFunction()
    template = SimpleNamespace(async_render=Mock(return_value="rendered"))
    hass.states.get.side_effect = lambda entity_id: SimpleNamespace(
        state="on",
        entity_id=entity_id,
    )

    result = await function.execute(
        hass,
        {"value_template": template, "parse_result": True},
        {"entity_ids": ["light.living_room", "switch.kitchen"]},
        None,
        exposed_entities,
    )

    assert result == "rendered"
    template.async_render.assert_called_once_with(
        {"entity_ids": ["light.living_room", "switch.kitchen"]},
        parse_result=True,
    )


@pytest.mark.asyncio
async def test_template_function_splits_comma_separated_entity_ids(
    hass, exposed_entities
) -> None:
    function = TemplateFunction()
    template = SimpleNamespace(async_render=Mock(return_value="ok"))
    hass.states.get.side_effect = lambda entity_id: SimpleNamespace(
        state="on",
        entity_id=entity_id,
    )

    await function.execute(
        hass,
        {"value_template": template},
        {"entity_id": " light.living_room, switch.kitchen, "},
        None,
        exposed_entities,
    )

    template.async_render.assert_called_once_with(
        {"entity_id": " light.living_room, switch.kitchen, "}, parse_result=False
    )


def test_knowledge_search_payload_omits_only_default_unused_filter() -> None:
    result = {
        "items": [{"id": "one"}],
        "source_filter": {
            "applied_source_ids": [],
            "ignored_source_ids": [],
            "fell_back_to_all_sources": False,
        },
    }

    compact = model_tool_results.knowledge_search_payload(
        result,
        filter_requested=False,
        policy_filter_applied=False,
    )

    assert compact == {"items": [{"id": "one"}]}
    assert "source_filter" in result


def test_knowledge_search_payload_preserves_filter_when_requested() -> None:
    result = {
        "source_filter": {
            "applied_source_ids": [],
            "ignored_source_ids": [],
            "fell_back_to_all_sources": False,
        }
    }

    assert (
        model_tool_results.knowledge_search_payload(
            result,
            filter_requested=True,
            policy_filter_applied=False,
        )
        is result
    )
    assert (
        model_tool_results.knowledge_search_payload(
            result,
            filter_requested=False,
            policy_filter_applied=True,
        )
        is result
    )


@pytest.mark.parametrize(
    "entity_ids",
    [
        None,
        [],
        [""],
        [123],
    ],
)
def test_history_request_rejects_invalid_entity_lists(entity_ids) -> None:
    with pytest.raises(HomeAssistantError, match="non-empty list"):
        safety_hardening._validate_history_request({"entity_ids": entity_ids})


def test_history_request_enforces_entity_count_limit() -> None:
    with pytest.raises(HomeAssistantError, match="at most"):
        safety_hardening._validate_history_request(
            {
                "entity_ids": [
                    f"sensor.entity_{index}"
                    for index in range(safety_hardening.MAX_HISTORY_ENTITY_IDS + 1)
                ]
            }
        )


def _canonical_skill_loader() -> dict:
    return {
        "spec": {"name": skill_availability.SKILL_LOADER_TOOL_NAME},
        "function": {
            "type": "read_file",
            "path": skill_availability.CANONICAL_SKILL_LOADER_PATH,
        },
    }


def test_effective_skill_loader_rejects_missing_group_loader_for_on_demand_group() -> (
    None
):
    loader = _canonical_skill_loader()
    groups = [
        {
            "id": "skills",
            "name": "Skills",
            "enabled": True,
            "loading_mode": FUNCTION_GROUP_LOADING_ON_DEMAND,
            "functions": [skill_availability.SKILL_LOADER_TOOL_NAME],
        }
    ]

    status = skill_availability.effective_skill_loader_status(
        ["weather"],
        ["weather"],
        [loader],
        groups,
        group_loader_supported=False,
    )

    assert not status.available
    assert status.on_demand
    assert status.group_id == "skills"
    assert status.loadable_skills == ("weather",)
    assert "Group loader" in status.reason


def test_selected_installed_skills_preserve_selection_order_and_deduplicate() -> None:
    assert skill_availability.selected_installed_skill_names(
        ["b", "missing", "a", "b"],
        ["a", "b"],
    ) == ("b", "a")
