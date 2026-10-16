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
from homeassistant.exceptions import HomeAssistantError
from homeassistant.core import SupportsResponse
from homeassistant.helpers import llm

from .test_model_tool_result_boundary import wire_result  # noqa: F401


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
