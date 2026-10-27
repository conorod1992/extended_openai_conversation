"""Failed Guest policy admission restores the enclosing request context."""

from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses import conversation as c
from custom_components.extended_openai_conversation_responses.guest_mode import (
    GuestCapabilityPolicy,
    GuestModeManager,
)
from custom_components.extended_openai_conversation_responses.ha_permissions import (
    bind_active_ha_context,
    get_active_ha_context,
)
from custom_components.extended_openai_conversation_responses.prompt_cache import (
    _PROMPT_CACHE_CONTEXT,
)
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError


def failing_child():
    a = object.__new__(c.ExtendedOpenAIAgentEntity)
    a.hass = SimpleNamespace(data={})
    a.subentry = SimpleNamespace(data={})
    a._get_configured_function_tools = lambda: []
    a._guest_mode = object.__new__(GuestModeManager)
    a._guest_mode._persistence_unavailable = True
    a._async_process = a._async_process_with_continuity
    return a


@pytest.mark.parametrize("has_enclosing_llm", [False, True])
async def test_guest_policy_failure_restores_context_and_enclosing_actual_user(
    has_enclosing_llm,
):
    """Exercise nested public APIs and genuine manager storage failure."""
    child = failing_child()
    parent = object.__new__(c.ExtendedOpenAIAgentEntity)
    parent_ha = Context(user_id="actual_parent")
    parent_llm = SimpleNamespace(context=parent_ha) if has_enclosing_llm else None
    child_llm = SimpleNamespace(context=Context(user_id="child"))
    parent_policy = GuestCapabilityPolicy.unrestricted()
    parent_config = ([], [])
    parent_cache = object()
    child_request = SimpleNamespace(as_llm_context=lambda _: child_llm)
    parent_request = SimpleNamespace(
        as_llm_context=lambda _: SimpleNamespace(context=parent_ha)
    )

    async def process(request):
        bindings = [
            (c._ACTIVE_LLM_CONTEXT, parent_llm),
            (c._ACTIVE_GUEST_POLICY, parent_policy),
            (c._ACTIVE_FUNCTION_CONFIG, parent_config),
            (_PROMPT_CACHE_CONTEXT, parent_cache),
        ]
        tokens = [(variable, variable.set(value)) for variable, value in bindings]
        try:
            with bind_active_ha_context(parent_ha):
                with pytest.raises(
                    HomeAssistantError, match="Guest Mode persistence is unavailable"
                ):
                    await child.async_process_direct(child_request)
                assert c._ACTIVE_LLM_CONTEXT.get() is parent_llm
                resolved = c._request_llm_context(request)
                assert resolved.context.user_id == "actual_parent"
                assert get_active_ha_context().user_id == "actual_parent"
                assert c._ACTIVE_GUEST_POLICY.get() is parent_policy
                assert c._ACTIVE_FUNCTION_CONFIG.get() is parent_config
                assert _PROMPT_CACHE_CONTEXT.get() is parent_cache
                return "parent remains usable"
        finally:
            for variable, token in reversed(tokens):
                variable.reset(token)

    parent._async_process = process
    assert await parent.async_process_direct(parent_request) == (
        "parent remains usable",
        {"handled_locally": False},
    )


@pytest.mark.parametrize("has_enclosing_llm", [False, True])
async def test_llm_conversion_failure_does_not_reset_an_unowned_token(
    has_enclosing_llm,
):
    child = failing_child()
    enclosing = (
        SimpleNamespace(context=Context(user_id="parent"))
        if has_enclosing_llm
        else None
    )
    original = ValueError("controlled input-conversion failure")

    def convert(_domain):
        raise original

    token = c._ACTIVE_LLM_CONTEXT.set(enclosing)
    config = object()
    config_token = c._ACTIVE_FUNCTION_CONFIG.set(config)
    try:
        with pytest.raises(ValueError) as error:
            await child.async_process_direct(SimpleNamespace(as_llm_context=convert))
        assert error.value is original
        assert c._ACTIVE_LLM_CONTEXT.get() is enclosing
        assert c._ACTIVE_FUNCTION_CONFIG.get() is config
    finally:
        c._ACTIVE_FUNCTION_CONFIG.reset(config_token)
        c._ACTIVE_LLM_CONTEXT.reset(token)
