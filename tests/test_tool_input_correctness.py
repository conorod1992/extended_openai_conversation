"""Regression tests for configured-tool input correctness."""

from __future__ import annotations

from pathlib import Path

import pytest

from homeassistant.components import conversation
from homeassistant.const import CONF_PAYLOAD, CONF_RESOURCE, CONF_RESOURCE_TEMPLATE
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.template import Template

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_RESPONSES,
    CONF_PAYLOAD_TEMPLATE,
)
from custom_components.extended_openai_conversation_responses.entity import (
    ExtendedOpenAIBaseLLMEntity,
    _convert_content_to_responses_param,
)
from custom_components.extended_openai_conversation_responses.exceptions import (
    InvalidFunction,
)
from custom_components.extended_openai_conversation_responses.function_execution import (
    validate_function_arguments,
)
from custom_components.extended_openai_conversation_responses.functions import (
    CompositeFunction,
)
from custom_components.extended_openai_conversation_responses.functions.web import (
    get_rest_data,
)


def _nested_service_spec() -> dict:
    """Return a representative nested configured-function schema."""
    return {
        "parameters": {
            "type": "object",
            "properties": {
                "delay": {
                    "type": "object",
                    "properties": {
                        "seconds": {"type": "integer", "minimum": 0},
                    },
                    "additionalProperties": False,
                },
                "list": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "properties": {
                            "domain": {
                                "type": "string",
                                "pattern": "^[a-z_]+$",
                            },
                            "service": {"type": "string", "minLength": 1},
                            "service_data": {
                                "type": "object",
                                "properties": {
                                    "entity_id": {
                                        "type": "array",
                                        "minItems": 1,
                                        "items": {
                                            "type": "string",
                                            "minLength": 1,
                                        },
                                    }
                                },
                                "additionalProperties": False,
                            },
                        },
                        "required": ["domain", "service", "service_data"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["list"],
            "additionalProperties": False,
        }
    }


def test_nested_function_arguments_are_validated_recursively() -> None:
    """Nested required fields and constraints are enforced before execution."""
    spec = _nested_service_spec()
    valid = {
        "delay": {"seconds": "2"},
        "list": [
            {
                "domain": "light",
                "service": "turn_on",
                "service_data": {"entity_id": ["light.kitchen"]},
            }
        ],
    }

    assert validate_function_arguments(spec, valid)["delay"]["seconds"] == 2

    missing_service = {
        "list": [
            {
                "domain": "light",
                "service_data": {"entity_id": ["light.kitchen"]},
            }
        ]
    }
    with pytest.raises(HomeAssistantError, match=r"list\[0\]\.service"):
        validate_function_arguments(spec, missing_service)

    with pytest.raises(HomeAssistantError, match=r"delay\.seconds.*at least 0"):
        validate_function_arguments(
            spec,
            {
                "delay": {"seconds": -1},
                "list": valid["list"],
            },
        )

    with pytest.raises(HomeAssistantError, match="at least 1 items"):
        validate_function_arguments(spec, {"list": []})

    bad_domain = {
        "list": [
            {
                "domain": "Light.Invalid",
                "service": "turn_on",
                "service_data": {"entity_id": ["light.kitchen"]},
            }
        ]
    }
    with pytest.raises(HomeAssistantError, match="required pattern"):
        validate_function_arguments(spec, bad_domain)

    unknown_nested = {
        "list": [
            {
                "domain": "light",
                "service": "turn_on",
                "service_data": {
                    "entity_id": ["light.kitchen"],
                    "unexpected": True,
                },
            }
        ]
    }
    with pytest.raises(HomeAssistantError, match=r"service_data\.unexpected"):
        validate_function_arguments(spec, unknown_nested)


def test_function_schema_without_explicit_object_type_still_validates() -> None:
    """Legacy schemas using properties without type retain validation."""
    spec = {
        "parameters": {
            "properties": {"count": {"type": "integer", "minimum": 1}},
            "required": ["count"],
        }
    }
    assert validate_function_arguments(spec, {"count": "2"}) == {"count": 2}
    with pytest.raises(HomeAssistantError, match="at least 1"):
        validate_function_arguments(spec, {"count": 0})


def test_rest_template_rendering_does_not_mutate_reusable_config(hass, monkeypatch) -> None:
    """Each REST invocation renders from the original template configuration."""
    resource_template = Template("https://example.test/{{ item }}", hass)
    payload_template = Template('{"item":"{{ item }}"}', hass)
    config = {
        CONF_RESOURCE_TEMPLATE: resource_template,
        CONF_PAYLOAD_TEMPLATE: payload_template,
    }
    captured: list[dict] = []

    def create_rest_data(_hass, rendered_config):
        captured.append(dict(rendered_config))
        return object()

    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.functions.web.rest.create_rest_data_from_config",
        create_rest_data,
    )

    get_rest_data(hass, config, {"item": "first"})
    get_rest_data(hass, config, {"item": "second"})

    assert config[CONF_RESOURCE_TEMPLATE] is resource_template
    assert config[CONF_PAYLOAD_TEMPLATE] is payload_template
    assert CONF_RESOURCE not in config
    assert CONF_PAYLOAD not in config
    assert captured[0][CONF_RESOURCE] == "https://example.test/first"
    assert captured[1][CONF_RESOURCE] == "https://example.test/second"
    assert captured[0][CONF_PAYLOAD] == '{"item":"first"}'
    assert captured[1][CONF_PAYLOAD] == '{"item":"second"}'


async def test_attachment_only_responses_turn_is_preserved(hass, tmp_path: Path) -> None:
    """An attachment-only user turn must not attach to an earlier message."""
    image_path = tmp_path / "camera.png"
    image_path.write_bytes(b"image-data")
    chat_log = conversation.ChatLog(hass, "conversation-id")
    chat_log.async_add_user_content(
        conversation.UserContent(
            content="",
            attachments=[
                conversation.Attachment(
                    media_content_id="media-source://camera/test",
                    mime_type="image/png",
                    path=image_path,
                )
            ],
        )
    )

    messages = _convert_content_to_responses_param(chat_log.content)
    assert messages[-1] == {
        "type": "message",
        "role": "user",
        "content": "",
    }

    entity = ExtendedOpenAIBaseLLMEntity.__new__(ExtendedOpenAIBaseLLMEntity)
    entity.hass = hass
    await entity._async_add_attachments(chat_log, messages, API_MODE_RESPONSES)

    assert messages[-1]["role"] == "user"
    assert len(messages[-1]["content"]) == 1
    assert messages[-1]["content"][0]["type"] == "input_image"
    assert messages[-1]["content"][0]["image_url"].startswith(
        "data:image/png;base64,"
    )


async def test_empty_composite_sequence_fails_cleanly(hass) -> None:
    """Empty composite functions are rejected without an unbound result crash."""
    function = CompositeFunction()

    with pytest.raises(InvalidFunction):
        function.validate_schema({"type": "composite", "sequence": []})

    with pytest.raises(HomeAssistantError, match="at least one function"):
        await function.execute(
            hass,
            {"type": "composite", "sequence": []},
            {},
            None,
            [],
        )
