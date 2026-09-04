"""Regression coverage for persisted Function Tool template hydration."""

from collections.abc import Mapping
from typing import Any

import pytest
import yaml

from homeassistant.helpers.template import Template

from custom_components.extended_openai_conversation_responses.const import (
    CONF_FUNCTION_TOOLS,
)
from custom_components.extended_openai_conversation_responses.functions import get_function
from custom_components.extended_openai_conversation_responses.performance import (
    _cached_configured_tools,
    cached_configured_function_tools_from_data,
)
from tests.helpers import load_function_tool_yaml


def _templates(value: Any) -> list[Template]:
    """Collect hydrated Home Assistant templates from nested runtime configs."""
    if isinstance(value, Template):
        return [value]
    if isinstance(value, Mapping):
        return [template for item in value.values() for template in _templates(item)]
    if isinstance(value, (list, tuple)):
        return [template for item in value for template in _templates(item)]
    return []


@pytest.mark.parametrize(
    "fixture_name",
    [
        "template_example.yaml",
        "bash_example.yaml",
        "read_file_example.yaml",
        "write_file_example.yaml",
        "edit_file_example.yaml",
        "rest_example.yaml",
        "scrape_example.yaml",
        "script_example.yaml",
        "composite_example.yaml",
    ],
)
def test_cached_template_bearing_tool_fixtures_remain_hydrated(
    hass, fixture_name: str
) -> None:
    """Every real template-bearing tool family stays hydrated across cache hits."""
    _cached_configured_tools.cache_clear()
    raw_tools = load_function_tool_yaml(fixture_name)
    data = {CONF_FUNCTION_TOOLS: yaml.safe_dump(raw_tools, sort_keys=False)}

    first = cached_configured_function_tools_from_data(data)
    second = cached_configured_function_tools_from_data(data)
    first_templates = _templates(first)
    second_templates = _templates(second)

    assert first_templates, f"Fixture {fixture_name} unexpectedly contains no templates"
    assert len(second_templates) == len(first_templates)
    assert all(template.hass is hass for template in first_templates)
    assert all(template.hass is hass for template in second_templates)
    assert all(isinstance(template, Template) for template in second_templates)
    assert first is not second
    assert first[0] is not second[0]

    cache_info = _cached_configured_tools.cache_info()
    assert cache_info.misses == 1
    assert cache_info.hits == 1


async def test_cached_persisted_template_tool_remains_executable(hass) -> None:
    """Cached runtime copies must not turn a hydrated Template back into a string."""
    _cached_configured_tools.cache_clear()
    tool = {
        "spec": {
            "name": "render_value",
            "description": "Render an argument",
            "parameters": {
                "type": "object",
                "properties": {"value": {"type": "string"}},
            },
        },
        "function": {
            "type": "template",
            "value_template": "{{ value }}",
        },
    }
    data = {CONF_FUNCTION_TOOLS: yaml.safe_dump([tool], sort_keys=False)}

    first = cached_configured_function_tools_from_data(data)
    second = cached_configured_function_tools_from_data(data)

    first_config = first[0]["function"]
    second_config = second[0]["function"]
    assert isinstance(first_config["value_template"], Template)
    assert isinstance(second_config["value_template"], Template)
    assert first_config["value_template"].hass is hass
    assert second_config["value_template"].hass is hass
    assert first is not second
    assert first[0] is not second[0]
    assert first_config is not second_config

    first_config["parse_result"] = True
    assert "parse_result" not in second_config

    result = await get_function("template").execute(
        hass,
        second_config,
        {"value": "working"},
        None,
        [],
    )
    assert result == "working"

    cache_info = _cached_configured_tools.cache_info()
    assert cache_info.misses == 1
    assert cache_info.hits == 1
