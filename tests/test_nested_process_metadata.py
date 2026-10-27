"""Public export, quarantine and nested-entry regressions from the follow-up audit."""


import pytest

from custom_components.extended_openai_conversation_responses import (
    conversation as owner,
)


@pytest.mark.parametrize("same_agent", [False, True])
@pytest.mark.parametrize("child_direct", [False, True])
async def test_nested_assist_invocations_cannot_write_parent_metadata(
    same_agent, child_direct
):
    parent = object.__new__(owner.ExtendedOpenAIAgentEntity)
    child = parent if same_agent else object.__new__(owner.ExtendedOpenAIAgentEntity)

    async def child_core(request):
        metadata = owner._ACTIVE_INVOCATION.get().metadata
        if metadata is not None:
            metadata.update(handled_locally=True, matched_rule={"id": "child-rule"})
        return "Child local answer"

    async def parent_core(request):
        if request == "child":
            return await child_core(request)
        if child_direct:
            _, metadata = await child.async_process_direct("child")
            assert metadata["handled_locally"] is True
        else:
            assert await child.async_process("child") == "Child local answer"
        return "Parent provider answered"

    parent._async_process = parent_core
    if not same_agent:
        child._async_process = child_core
    result, metadata = await parent.async_process_direct("parent")
    assert result == "Parent provider answered"
    assert metadata == {"handled_locally": False}
    assert owner._ACTIVE_INVOCATION.get().metadata is None
