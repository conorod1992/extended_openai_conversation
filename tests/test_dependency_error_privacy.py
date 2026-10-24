"""Real dependency failures and history repair never publish backend source."""

import asyncio
import json
from types import SimpleNamespace

from bs4 import BeautifulSoup
import pytest

from custom_components.extended_openai_conversation_responses.agent_config import (
    validate_function_tools,
)
from custom_components.extended_openai_conversation_responses.entity import (
    ExtendedOpenAIBaseLLMEntity,
)
from custom_components.extended_openai_conversation_responses.function_call_budget import (
    FunctionCallBudget,
)
from custom_components.extended_openai_conversation_responses.function_tool_recovery import (
    ToolRecoveryState,
)
from custom_components.extended_openai_conversation_responses.functions import web
from custom_components.extended_openai_conversation_responses.public_errors import (
    SanitizedUnexpectedBackendError,
    sanitize_backend_error,
)
from custom_components.extended_openai_conversation_responses.tool_exchange import (
    append_unresolved_tool_results,
    async_execute_tool_exchange,
)
from homeassistant.components import conversation
from homeassistant.helpers import llm

from .test_model_tool_result_boundary import wire_result  # noqa: F401


@pytest.mark.parametrize("strict", [False, True])
async def test_scrape_selector_failure_is_private_in_retained_history(
    hass, monkeypatch, wire_result, strict
):
    secret = "FABRICATED-private-selector-token-42"
    tool = {
        "spec": {
            "name": "download",
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {
            "type": "scrape",
            "resource": "https://example.invalid/account",
            "sensor": [
                {
                    "name": "download",
                    "select": 'a[href="https://example.invalid/?token=' + secret + '"',
                }
            ],
        },
    }
    validate_function_tools([tool])
    entity = SimpleNamespace(
        hass=hass,
        entity_id="conversation.privacy",
        should_run_in_background=lambda _: False,
    )
    entity._execute_function_tool = lambda *args: (
        ExtendedOpenAIBaseLLMEntity._execute_function_tool(entity, *args)
    )
    call = llm.ToolInput(
        id="private", tool_name="download", tool_args={}, external=True
    )
    log = conversation.ChatLog(hass, "private")
    log.async_add_assistant_content_without_tools(
        conversation.AssistantContent(agent_id="agent", tool_calls=[call])
    )

    class Coordinator:
        def __init__(self, *args, **kwargs):
            self.data = BeautifulSoup('<a href="/">Download</a>', "html.parser")

        async def async_refresh(self):
            pass

    monkeypatch.setattr(web, "get_rest_data", lambda *args: None)
    monkeypatch.setattr(web.scrape.coordinator, "ScrapeCoordinator", Coordinator)

    async def execute():
        await async_execute_tool_exchange(
            entity,
            log,
            [call],
            [tool],
            FunctionCallBudget(2),
            None,
            [],
            recovery_state=ToolRecoveryState(enabled=strict),
        )

    with pytest.raises(SanitizedUnexpectedBackendError):
        await execute()
    public = json.dumps(wire_result(log.content[-1]))
    assert secret not in public
    assert "Malformed attribute selector" not in public
    assert "Check the tool configuration and permissions" in public


@pytest.mark.parametrize(
    "error_type",
    [
        ValueError,
        TypeError,
        RuntimeError,
        asyncio.CancelledError,
        KeyboardInterrupt,
        SystemExit,
    ],
)
def test_unexpected_history_repair_error_is_private(hass, wire_result, error_type):
    call = llm.ToolInput(id="repair", tool_name="probe", tool_args={}, external=True)
    log = conversation.ChatLog(hass, "repair")
    log.async_add_assistant_content_without_tools(
        conversation.AssistantContent(agent_id="agent", tool_calls=[call])
    )
    secret = "FABRICATED-private-dependency-value-42"
    append_unresolved_tool_results(log, "agent", [call], error=error_type(secret))
    assert secret not in json.dumps(wire_result(log.content[-1]))
    assert wire_result(log.content[-1])["result"]["status"] == "error"


@pytest.mark.parametrize(
    "error", [asyncio.CancelledError(), KeyboardInterrupt(), SystemExit()]
)
def test_process_and_cancellation_controls_are_not_reclassified(error):
    assert sanitize_backend_error(error) is error
