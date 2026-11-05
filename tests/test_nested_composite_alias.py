"""A parent owns each Composite result alias through hydration and persistence."""

from copy import deepcopy

import pytest
import yaml

from custom_components.extended_openai_conversation_responses.agent_config import (
    validate_function_tools,
)
from custom_components.extended_openai_conversation_responses.functions import (
    CompositeFunction,
)


@pytest.mark.parametrize("representation", ["authored", "hydrated", "persisted"])
@pytest.mark.parametrize("depth", [1, 2, 3])
async def test_nested_composite_alias_reaches_the_next_step(
    hass, representation, depth
):
    config = {"type": "template", "value_template": "inner"}
    for level in range(depth):
        config = {
            "type": "composite",
            "sequence": [
                {**config, "response_variable": f"result_{level}"},
                {
                    "type": "template",
                    "value_template": "{{ result_" + str(level) + " }}",
                },
            ],
        }
    source = deepcopy(config)
    hydrated = validate_function_tools(
        [
            {
                "spec": {
                    "name": "nested_demo",
                    "parameters": {"type": "object", "properties": {}},
                },
                "function": config,
            }
        ]
    )[0]["function"]
    executable = config if representation == "authored" else hydrated
    if representation == "persisted":
        executable = yaml.safe_load(yaml.safe_dump(deepcopy(hydrated)))
    assert await CompositeFunction().execute(hass, executable, {}, None, []) == "inner"
    assert config == source
    assert deepcopy(hydrated) == source


async def test_nested_alias_does_not_relax_recursive_preflight(hass, tmp_path):
    output = tmp_path / "effect.txt"
    config = {
        "type": "composite",
        "sequence": [
            {
                "type": "write_file",
                "path": str(output),
                "content": "must not execute",
                "allow_dir": [str(tmp_path)],
            },
            {
                "type": "composite",
                "response_variable": "result",
                "sequence": [
                    {"type": "template", "value_template": "inner", "unexpected": True},
                ],
            },
        ],
    }
    from homeassistant.exceptions import HomeAssistantError

    with pytest.raises(HomeAssistantError):
        await CompositeFunction().execute(hass, config, {}, None, [])
    assert not output.exists()
