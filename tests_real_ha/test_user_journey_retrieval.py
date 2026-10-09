"""Naturalistic Knowledge retrieval with distractors and disabled sources."""
import json

from custom_components.extended_openai_conversation_responses.const import API_MODE_CHAT_COMPLETIONS
from tests_real_ha.test_knowledge_provider_wire_e2e import (
    _knowledge_agent, _say, _chat_sse_tool_call, _chat_sse_text,
    _chat_tool_result,
)
from tests_real_ha.test_provider_wire_e2e import _install_wire, _speech


async def test_appliance_procedure_retrieval_excludes_disabled_distractor(hass, monkeypatch):
    agent = await _knowledge_agent(hass, API_MODE_CHAT_COMPLETIONS)
    knowledge = agent._knowledge
    relevant = await knowledge.async_create(
        "Dishwasher drainage and filter procedure",
        "Instructions for a dishwasher refusing to drain",
        "If the dishwasher will not drain, isolate power and check the sump filter. "
        "The dishwasher reset indicator is blue-purple after filter cleaning.",
    )
    await knowledge.async_create(
        "Washing machine drainage",
        "A different appliance and drain pump",
        "Washing machine drain problems require checking the pump trap.",
    )
    disabled = await knowledge.async_create(
        "Outdated dishwasher procedure",
        "Superseded dishwasher drainage instructions",
        "The old dishwasher instruction said to bypass the filter sensor.",
    )
    await knowledge.async_update(disabled.source_id, enabled=False)
    wire = _install_wire(
        monkeypatch, agent,
        [_chat_sse_tool_call("call-appliance", "knowledge_search",
                            {"query": "dishwasher will not drain filter", "limit": 5}),
         _chat_sse_text("Search completed.")],
    )
    assert _speech(await _say(hass, agent, "How should I troubleshoot the dishwasher drainage?")) == "Search completed."
    result = _chat_tool_result(wire.requests[1]["body"], "call-appliance")
    identifiers = {item["source_id"] for item in result["results"]}
    assert relevant.source_id in identifiers
    assert disabled.source_id not in identifiers
    assert "bypass the filter sensor" not in json.dumps(result)
