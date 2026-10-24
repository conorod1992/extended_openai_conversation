"""Case-insensitive captures become canonical reasoning enums, not model names."""

import pytest
from custom_components.extended_openai_conversation_responses.request_rules import (
    RequestRules,
    RequestRuleRuntime,
    async_evaluate_rule,
)
from custom_components.extended_openai_conversation_responses.request import (
    build_provider_request_snapshot,
)
from tests.test_request_rule_routing_semantics import MemoryStore, _rule


@pytest.mark.parametrize(
    "text,display",
    [
        ("set reasoning to high", "high"),
        ("set reasoning to High", "High"),
        ("SET REASONING TO HIGH", "HIGH"),
    ],
)
@pytest.mark.parametrize("azure", [False, True])
async def test_captured_effort_is_canonical_before_validation_and_storage(
    hass, text, display, azure
):
    model = "Home-Deployment" if azure else "gpt-5"
    entry = {"api_provider": "azure"} if azure else {}
    options = {
        "chat_model": model,
        "reasoning_effort": "low",
        "api_mode": "responses",
        "max_function_calls_per_conversation": 0,
        "continue_conversation": "ha_default",
    }
    if azure:
        options["azure_model"] = "gpt-5"
    rule = _rule(
        "effort",
        "set reasoning to {effort=low|medium|high}",
        match_type="sentence_pattern",
        action={
            "model": model,
            "reasoning_effort": "{effort}",
            "scope": "conversation",
            "reset": False,
            "continue_to_ai": False,
            "success_response": "Using {effort}",
        },
    )
    rules = RequestRules(MemoryStore({"rules": [rule]}))
    await rules.async_initialize()
    runtime = RequestRuleRuntime()
    result = await async_evaluate_rule(
        hass,
        rules,
        runtime,
        text,
        "session",
        model,
        request_options=options,
        entry_data=entry,
    )
    assert result is not None and result.consume
    assert result.match.slots["effort"] == display
    saved = runtime.get("session")
    assert saved == {"chat_model": model, "reasoning_effort": "high"}
    snapshot = build_provider_request_snapshot(
        {**options, **saved}, entry, tools_required=False
    )
    assert snapshot.api_kwargs["reasoning"]["effort"] == "high"
