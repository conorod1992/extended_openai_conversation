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
@pytest.mark.parametrize("value", [False, 0, None, 73])
async def test_result_namespace_with_native_collision(
    tmp_path, producer, value
):
    hass = HomeAssistant(str(tmp_path))
    hass.config.legacy_templates = False
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
        DOMAIN, "call_function", capture, supports_response=SupportsResponse.OPTIONAL
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
        {"variables": {"native_reading": {"nested": [99]}}},
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
        {"action": "namespace_probe.record", "data": {"value": "{{ native_reading.nested[0] }}"}},
    ]
    rule["action"]["success_response"] = "{first_result.level}: {reading.nested.0}"
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
        assert effects == [{"value": 99}]
        if producer == "success":
            import json

            assert result.successful
            assert result.response == f"11: {json.dumps(value)}"
        else:
            assert not result.successful
            assert "response construction failed (Function result reading is unavailable)" in result.response
    finally:
        await hass.async_stop(force=True)
