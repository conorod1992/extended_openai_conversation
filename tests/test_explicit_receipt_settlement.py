from types import SimpleNamespace
from custom_components.extended_openai_conversation_responses import (
    execution_outcomes as outcomes,
)


def test_none_settlement_cannot_reinterpret_an_earlier_effect():
    entity = SimpleNamespace()
    call = SimpleNamespace(id="effect", tool_name="effect", tool_args={})
    tool = {"spec": {"name": "effect", "parameters": {"type": "object"}}}
    with outcomes.execution_attempt(entity, call, tool):
        first = outcomes.execution_started()
        second = outcomes.execution_started()
        outcomes.execution_completed(first)
        outcomes.execution_completed(None)
        outcomes.execution_rejected(None)
        assert first.outcome == "completed"
        assert second.outcome == "uncertain"
        outcomes.execution_refused()
        assert second.outcome == "uncertain"
    assert entity._execution_tool_outcomes[id(call)] == "uncertain"


async def test_none_rejection_keeps_lost_service_acknowledgement_protected(
    hass, monkeypatch
):
    from custom_components.extended_openai_conversation_responses.exceptions import (
        EffectReplayBlocked,
    )
    from custom_components.extended_openai_conversation_responses.entity import (
        ExtendedOpenAIBaseLLMEntity,
    )
    from custom_components.extended_openai_conversation_responses.tool_replay_guard import (
        bind_effect_replay_scope,
        remember_unacknowledged_calls,
    )
    from homeassistant.exceptions import HomeAssistantError
    from tests.test_function_tool_error_recovery import _call, _chat_log, _retain_calls
    from tests.test_review_replay_execution import (
        RAW,
        _entity,
        _tool,
        _execute,
        _install_native_boundary,
    )

    _install_native_boundary(hass, monkeypatch)
    entity, log, tool = _entity(hass), _chat_log(hass), _tool()
    first = _call("first", "effect", RAW)
    _retain_calls(log, [first])
    effects = []

    async def service(*args, **kwargs):
        effects.append("toggle")
        raise HomeAssistantError("lost acknowledgement after effect")

    hass.services.async_call.side_effect = service
    with outcomes.execution_attempt(entity, first, tool):
        await ExtendedOpenAIBaseLLMEntity._execute_function_tool(
            entity, tool, first, None, []
        )
        outcomes.execution_rejected(None)
    remember_unacknowledged_calls(entity, log, set())
    with bind_effect_replay_scope(entity, log.conversation_id):
        try:
            await _execute(entity, tool, _call("new-provider-call-id", "effect", RAW))
        except EffectReplayBlocked:
            pass
    assert effects == ["toggle"]
