"""Handled backend failures retain provenance when model output overflows."""

from copy import deepcopy
from dataclasses import asdict, dataclass
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import (
    entity as entity_module,
)
from custom_components.extended_openai_conversation_responses.entity import (
    ExtendedOpenAIBaseLLMEntity,
)
from custom_components.extended_openai_conversation_responses.function_execution import (
    backend_failure,
)
from custom_components.extended_openai_conversation_responses.ha_tool_result_compat import (
    ExecutionFailure,
    make_tool_result_content,
    tool_result_data,
)
from custom_components.extended_openai_conversation_responses.model_tool_results import (
    OwnedJSONPayload,
    project_tool_result,
)
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import llm

from .test_model_tool_result_boundary import wire_result  # noqa: F401


@pytest.mark.parametrize(
    "value", [b"ordinary binary response", {"ordinary set response"}, 2**128, {1: "one"}]
)
async def test_mixed_native_batch_fallback_preserves_private_failure(
    hass, exposed_entities, wire_result, value
):
    secret = "FABRICATED-private-service-credential-42"
    hass.services.async_call.side_effect = [
        HomeAssistantError("Backend password=" + secret),
        {"report": value},
    ]
    hass.services.supports_response.return_value = SupportsResponse.OPTIONAL
    argument = {
        "domain": "light",
        "service": "turn_on",
        "service_data": {"entity_id": "light.living_room"},
    }
    content = await execute_configured(
        hass, exposed_entities, "execute_service", {"list": [argument, argument]}
    )
    retained = tool_result_data(content)["result"]
    expected = {"1": "one"} if isinstance(value, dict) else str(value)
    assert retained[0]["error"] == "Backend password=" + secret
    assert isinstance(retained[0], ExecutionFailure)
    assert retained[1]["response"]["report"] == expected
    for prepared in (content, await project_tool_result(content, prepare_wire=True)):
        wire = wire_result(prepared)["result"]
        assert secret not in json.dumps(wire)
        assert "Check the tool configuration and permissions" in wire[0]["error"]
        assert wire[1]["response"]["report"] == expected
    assert hass.services.async_call.await_count == 2


def test_recursive_and_deep_fallback_cannot_erase_failure_provenance():
    from custom_components.extended_openai_conversation_responses.entity import (
        _normalize_function_result,
    )
    from custom_components.extended_openai_conversation_responses.ha_tool_result_compat import (
        BackendExecutionFailure,
    )

    recursive = []
    recursive.append(recursive)
    result = _normalize_function_result(recursive)
    assert isinstance(result[0], BackendExecutionFailure)
    nested = b"unsupported"
    for _ in range(40):
        nested = [nested]
    result = _normalize_function_result(nested)
    for _ in range(32):
        result = result[0]
    assert isinstance(result, BackendExecutionFailure)


async def execute_configured(hass, exposed_entities, name, arguments):
    entity = SimpleNamespace(
        hass=hass,
        entity_id="ai_task.backend_probe",
        should_run_in_background=lambda _: False,
    )
    call = llm.ToolInput(id="backend", tool_name="native", tool_args=arguments)
    tool = {
        "spec": {
            "name": "native",
            "parameters": {"type": "object", "additionalProperties": True},
        },
        "function": {"type": "native", "name": name},
    }
    return await ExtendedOpenAIBaseLLMEntity._execute_function_tool(
        entity, tool, call, None, exposed_entities
    )


@pytest.mark.parametrize("batch", [False, True])
async def test_small_native_failure_diagnostics_are_private_on_both_wires(
    hass, exposed_entities, wire_result, batch
):
    secret = "unfamiliar private backend credential 42"
    hass.services.async_call.side_effect = HomeAssistantError(secret)
    argument = {
        "domain": "light",
        "service": "turn_on",
        "service_data": {"entity_id": "light.living_room"},
    }
    content = await execute_configured(
        hass,
        exposed_entities,
        "execute_service" if batch else "execute_service_single",
        {"list": [argument]} if batch else argument,
    )
    retained = deepcopy(tool_result_data(content))
    assert secret in json.dumps(retained)  # Local diagnostics remain complete.
    for result in (content, await project_tool_result(content, prepare_wire=True)):
        public = json.dumps(wire_result(result))
        assert secret not in public
        assert "Check the tool configuration and permissions" in public
    assert tool_result_data(content) == retained


@pytest.mark.parametrize("owned", [False, True])
async def test_nested_marked_failure_is_contained_without_rewriting_business_data(
    wire_result, owned
):
    secret = "unfamiliar private backend credential 42"
    value = {
        "items": [{"nested": backend_failure(secret)}],
        "business": {"error": "legitimate result"},
    }
    content = make_tool_result_content(
        agent_id="probe",
        tool_call_id="nested",
        tool_name="native",
        tool_result=OwnedJSONPayload(value) if owned else {"result": value},
    )
    for result in (content, await project_tool_result(content, prepare_wire=True)):
        payload = wire_result(result)["result"]
        payload = json.loads(payload) if isinstance(payload, str) else payload
        assert secret not in json.dumps(payload)
        assert payload["business"] == {"error": "legitimate result"}
    assert value["items"][0]["nested"]["error"] == secret


@pytest.mark.parametrize("batch", [False, True])
async def test_native_handled_failures_survive_both_model_boundaries(
    hass, exposed_entities, wire_result, batch
):
    argument = {
        "domain": "light",
        "service": "turn_on",
        "service_data": {"entity_id": "light.living_room"},
    }
    if batch:
        # A short failure plus a large success must retain the failed/partial
        # outcome too: a huge error message is not required to trigger the bug.
        hass.services.async_call.side_effect = [
            HomeAssistantError("ordinary error"),
            {"report": "x" * 64000},
        ]
        hass.services.supports_response.return_value = SupportsResponse.OPTIONAL
        name, arguments = "execute_service", {"list": [argument, argument]}
    else:
        hass.services.async_call.side_effect = HomeAssistantError("x" * 64000)
        name, arguments = "execute_service_single", argument
    content = await execute_configured(hass, exposed_entities, name, arguments)
    retained = tool_result_data(content)["result"]
    failure = retained[0] if batch else retained
    assert isinstance(failure, ExecutionFailure)
    assert failure == {"error": "ordinary error" if batch else "x" * 64000}
    if batch:
        assert len(json.dumps(retained[1])) > 64000
    for result in (content, await project_tool_result(content)):
        for _ in range(2):
            overflow = wire_result(result)["result"]
            assert overflow["status"] == "output_overflow"
            assert overflow["error"] is True
            assert "do not repeat side effects" in overflow["message"]
    assert tool_result_data(content)["result"] is retained
    assert hass.services.async_call.await_count == (2 if batch else 1)


@pytest.mark.parametrize("owned", [False, True])
async def test_nested_owned_failure_survives_copy_and_ha_dataclass_projection(
    wire_result, owned
):
    @dataclass
    class Record:
        result: dict

    failure = backend_failure("ordinary error")
    value = asdict(
        Record(
            deepcopy({"batches": [[{"success": True}, failure]], "report": "x" * 64000})
        )
    )
    assert isinstance(value["result"]["batches"][0][1], ExecutionFailure)
    content = make_tool_result_content(
        agent_id="probe",
        tool_call_id="nested",
        tool_name="native",
        tool_result=OwnedJSONPayload(value) if owned else {"result": value},
    )
    for result in (content, await project_tool_result(content)):
        overflow = wire_result(result)["result"]
        if isinstance(overflow, str):
            overflow = json.loads(overflow)
        assert overflow["error"] is True
        assert overflow["status"] == "output_overflow"


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("large", [False, True])
@pytest.mark.parametrize(
    "business_error", ["ordinary error field", True, {"status": "error"}]
)
async def test_plain_error_fields_remain_business_data(
    hass, exposed_entities, monkeypatch, wire_result, nested, large, business_error
):
    business = {"error": business_error}
    if isinstance(business_error, dict):
        business = business_error
    if large:
        business = {**business, "report": "x" * 64000}
    value = {"batches": [[{"success": True}, business]]} if nested else business
    backend = SimpleNamespace(execute=AsyncMock(return_value=value))
    monkeypatch.setattr(entity_module, "get_function", lambda _: backend)
    content = await execute_configured(
        hass, exposed_entities, "execute_service_single", {}
    )
    assert tool_result_data(content)["result"] is value
    for result in (content, await project_tool_result(content)):
        output = wire_result(result)["result"]
        if large:
            assert output["status"] == "output_overflow"
            assert output.get("error", False) is False
        else:
            assert output == value
    backend.execute.assert_awaited_once()
