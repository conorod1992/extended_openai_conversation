"""Regression coverage for persisted Function Tool template hydration."""

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
