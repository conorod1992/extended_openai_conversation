"""Accepted patterned keys must not be rejected as additional properties."""

from copy import deepcopy

from jsonschema import Draft202012Validator
import pytest

from custom_components.extended_openai_conversation_responses import regex_execution
from custom_components.extended_openai_conversation_responses.agent_config import (
    validate_function_tools,
)
from custom_components.extended_openai_conversation_responses.exceptions import (
    FunctionValidationInfrastructureError,
)
from custom_components.extended_openai_conversation_responses.function_dependency_integrity import (
    async_validate_static_function_arguments,
)
from custom_components.extended_openai_conversation_responses.function_execution import (
    async_validate_function_arguments,
    validate_function_arguments,
    validate_function_schema,
)
from custom_components.extended_openai_conversation_responses.model_payload import (
    prepare_model_function_tools,
)
from homeassistant.exceptions import HomeAssistantError


@pytest.mark.parametrize("layout", ["payload", "array", "root"])
@pytest.mark.parametrize("nullable", [False, True])
async def test_patterned_schema_admission_advertisement_and_consumption(
    hass, layout, nullable
):
    pattern = "^item_[0-9]{2}$" if nullable else "^[a-z]+$"
    value = (
        {"item_01": "ready", "item_99": None} if nullable else {"x": 0, "x" * 32: 100}
    )
    child = {
        "type": "object",
        "patternProperties": {
            pattern: {"type": ["string", "null"] if nullable else "integer"}
        },
        "additionalProperties": False,
        "minProperties": 2,
        "maxProperties": 2,
    }
    if layout == "root":
        schema, arguments = child, value
    else:
        schema = {
            "type": "object",
            "properties": {
                "payload": child
                if layout == "payload"
                else {"type": "array", "items": child}
            },
            "required": ["payload"],
            "additionalProperties": False,
        }
        arguments = {"payload": value if layout == "payload" else [value]}
    before = deepcopy((schema, arguments))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(arguments)
    (tool,) = validate_function_tools(
        [
            {
                "spec": {"name": "pattern_probe", "parameters": schema},
                "function": {"type": "template", "value_template": "accepted"},
            }
        ]
    )
    assert any(
        "patternProperties" in warning for warning in validate_function_schema(schema)
    )
    assert prepare_model_function_tools([tool])[0]["spec"]["parameters"] == schema
    assert validate_function_arguments(tool["spec"], arguments) == arguments
    assert (
        await async_validate_function_arguments(hass, tool["spec"], arguments)
        == arguments
    )
    await async_validate_static_function_arguments(hass, tool["spec"], arguments)
    assert (schema, arguments) == before


@pytest.mark.parametrize("additional", [False, {"type": "integer", "minimum": 10}])
def test_pattern_membership_preserves_other_supported_assertions(additional):
    schema = {
        "type": "object",
        "properties": {"fixed": {"type": "integer", "minimum": 2}},
        "patternProperties": {"item_": {"type": "string"}},
        "additionalProperties": additional,
    }
    spec = {"parameters": schema}
    # Unanchored regex search is JSON Schema's matching rule, including overlap
    # with a declared name; declared-field validation must remain effective.
    assert validate_function_arguments(
        spec, {"fixed": "3", "prefix_item_suffix": None}
    ) == {"fixed": 3, "prefix_item_suffix": None}
    with pytest.raises(HomeAssistantError, match="at least 2"):
        validate_function_arguments(spec, {"fixed": 1, "item_x": "ok"})
    with pytest.raises(
        HomeAssistantError,
        match="Unknown function input" if additional is False else "at least 10",
    ):
        validate_function_arguments(spec, {"unmatched": 1})


def test_overlapping_patterns_and_explicit_properties_do_not_drop_data():
    spec = {
        "parameters": {
            "type": "object",
            "properties": {"x": {"type": "integer"}},
            "patternProperties": {"^x": {"type": "integer"}, "x$": {"minimum": 0}},
            "additionalProperties": False,
        }
    }
    assert validate_function_arguments(spec, {"x": 1, "xx": 2}) == {"x": 1, "xx": 2}


@pytest.mark.parametrize(
    "result", [{"results": [], "invalid": []}, {"results": ["true"], "invalid": []}]
)
def test_incomplete_or_invalid_worker_proof_cannot_admit_keys(monkeypatch, result):
    monkeypatch.setattr(regex_execution, "_run_regex_worker", lambda _: result)
    with pytest.raises(HomeAssistantError, match="regular expression returned"):
        validate_function_arguments(
            {
                "parameters": {
                    "type": "object",
                    "patternProperties": {"x": {}},
                    "additionalProperties": False,
                }
            },
            {"x": 1},
        )


def test_key_regexes_keep_the_hard_worker_deadline(monkeypatch):
    monkeypatch.setattr(regex_execution, "_CONFIGURED_REGEX_TIMEOUT_SECONDS", 0.1)
    with pytest.raises(HomeAssistantError, match="execution limit"):
        validate_function_arguments(
            {
                "parameters": {
                    "type": "object",
                    "patternProperties": {"(a+)+$": {}},
                    "additionalProperties": False,
                }
            },
            {"a" * 100 + "!": 1},
        )


def test_invalid_pattern_cannot_be_treated_as_an_allowed_key():
    with pytest.raises(
        HomeAssistantError, match="Invalid configured regular expression"
    ):
        validate_function_arguments(
            {
                "parameters": {
                    "type": "object",
                    "patternProperties": {"[": {}},
                    "additionalProperties": False,
                }
            },
            {"x": 1},
        )


@pytest.mark.parametrize("dynamic", [False, True])
async def test_patterned_keys_do_not_inherit_additional_value_patterns(hass, dynamic):
    spec = {
        "parameters": {
            "type": "object",
            "properties": {"fixed": {"type": "integer"}},
            "patternProperties": {"^item_": {"type": "string"}},
            "additionalProperties": {"type": "string", "pattern": "^extra_"},
        }
    }
    arguments = {
        "fixed": "{{ future }}" if dynamic else 3,
        "item_01": "allowed",
        "other": "extra_ok",
    }
    if dynamic:
        await async_validate_static_function_arguments(hass, spec, arguments)
    else:
        assert (
            await async_validate_function_arguments(hass, spec, arguments) == arguments
        )
    bad = {**arguments, "other": "invalid"}
    with pytest.raises(HomeAssistantError, match="required pattern"):
        if dynamic:
            await async_validate_static_function_arguments(hass, spec, bad)
        else:
            await async_validate_function_arguments(hass, spec, bad)


async def test_worker_failure_is_not_a_correctable_argument_error(hass, monkeypatch):
    monkeypatch.setattr(
        regex_execution, "_run_regex_worker", lambda _: {"results": [], "invalid": []}
    )
    with pytest.raises(FunctionValidationInfrastructureError):
        await async_validate_function_arguments(
            hass,
            {
                "parameters": {
                    "type": "object",
                    "patternProperties": {"x": {}},
                    "additionalProperties": False,
                }
            },
            {"x": 1},
            distinguish_infrastructure=True,
        )


@pytest.mark.parametrize("additional", [True, {}])
def test_permissive_additional_schema_needs_no_pattern_proof(monkeypatch, additional):
    def unused(_):
        pytest.fail("Permissive additional properties require no key classification")

    monkeypatch.setattr(regex_execution, "_run_regex_worker", unused)
    spec = {
        "parameters": {
            "type": "object",
            "patternProperties": {"(a+)+$": {}},
            "additionalProperties": additional,
        }
    }
    assert validate_function_arguments(spec, {"x": 1}) == {"x": 1}
