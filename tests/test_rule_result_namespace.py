"""Dedicated Function references never read unrelated native script variables."""

import pytest

from custom_components.extended_openai_conversation_responses import (
    request_rules as rules,
)
from custom_components.extended_openai_conversation_responses.const import DOMAIN
from homeassistant.core import HomeAssistant, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from tests.test_request_rules import local_rule, manager


@pytest.mark.parametrize("producer", ["disabled", "failed", "success"])
@pytest.mark.parametrize("embedded", [False, True, "native"])
@pytest.mark.parametrize("value", [False, 0, None, 73])
async def test_result_namespace_with_native_collision(
    tmp_path, producer, embedded, value
):
    hass = HomeAssistant(str(tmp_path))
    effects, calls = [], []

    async def capture(call):
        return {
            "result": await rules.async_call_active_function(
                call.data["function"], {}, call.data["result_alias"]
            )
        }

    async def record(call):
        effects.append(dict(call.data))

    async def execute(function, arguments):
        calls.append(function)
        if function == "first":
            return {"level": 11}
        if producer == "failed":
            raise HomeAssistantError("lookup failed")
        return {"nested": [value]}

    hass.services.async_register(
        DOMAIN, "call_function", capture, supports_response=SupportsResponse.ONLY
    )
    hass.services.async_register("namespace_probe", "record", record)

    def function(name, alias, **extra):
        return {
            "action": f"{DOMAIN}.call_function",
            "data": {"function": name, "arguments": {}, "result_alias": alias},
            **extra,
        }

    rule = local_rule(phrases=["run"])
    rule["action"]["actions"] = [
        {"variables": {"reading": {"nested": [99]}}},
        function("first", "first_result"),
        function(
            "lookup",
            "reading",
            **(
                {"enabled": False}
                if producer == "disabled"
                else {"continue_on_error": True}
            ),
        ),
        {"action": "namespace_probe.record", "data": {"value": "{first_result.level}"}},
        {
            "action": "namespace_probe.record",
            "data": {
                "value": "Reading: {reading.nested.0}"
                if embedded
                else "{reading.nested.0}"
            },
        },
    ]
    if embedded == "native":
        rule["action"]["actions"][-1]["data"]["value"] = "{{ reading.nested[0] }}"
    try:
        result = await rules.async_evaluate_rule(
            hass,
            await manager(rule),
            rules.RequestRuleRuntime(),
            "run",
            "session",
            function_executor=execute,
        )
        assert calls == (["first"] if producer == "disabled" else ["first", "lookup"])
        assert effects[0] == {"value": 11}
        assert result.successful == (producer == "success" or embedded == "native")
        if embedded == "native":
            assert effects[1] == {"value": value if producer == "success" else 99}
        elif producer == "success":
            assert effects[1] == {"value": f"Reading: {value}" if embedded else value}
            if not embedded:
                assert type(effects[1]["value"]) is type(value)
        else:
            assert len(effects) == 1
    finally:
        await hass.async_stop(force=True)
