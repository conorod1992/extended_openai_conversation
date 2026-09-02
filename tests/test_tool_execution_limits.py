"""Tests for request-local function execution limits and loop exhaustion."""

from types import SimpleNamespace

from homeassistant.exceptions import HomeAssistantError
import pytest

from custom_components.extended_openai_conversation_responses.runtime_cleanup import (
    FunctionCallBudget,
    _assert_tool_loop_completed,
    _budgeted_function_tools,
)


def test_function_call_budget_counts_individual_calls() -> None:
    """Parallel calls consume one slot each rather than one slot for the round."""
    budget = FunctionCallBudget(limit=2)

    budget.claim("first")
    budget.claim("second")

    assert budget.used == 2
    assert budget.exhausted is True
    with pytest.raises(HomeAssistantError, match="Function call limit of 2 reached"):
        budget.claim("third")
    assert budget.used == 2


def test_function_call_budget_allows_unlimited_negative_limit() -> None:
    """The existing negative-limit convention continues to mean unlimited."""
    budget = FunctionCallBudget(limit=-1)

    for index in range(100):
        budget.claim(f"tool_{index}")

    assert budget.used == 100
    assert budget.exhausted is False


def test_budgeted_function_tools_stop_after_actual_budget_is_used() -> None:
    """Later provider rounds stop advertising functions once calls consume the cap."""
    calls = 0

    def factory() -> list[dict]:
        nonlocal calls
        calls += 1
        return [{"spec": {"name": "do_work"}}]

    budget = FunctionCallBudget(limit=2)
    assert _budgeted_function_tools(budget, factory)
    budget.claim("first")
    budget.claim("second")

    assert _budgeted_function_tools(budget, factory) == []
    assert calls == 1


def test_zero_budget_never_advertises_ordinary_functions() -> None:
    """A configured zero-call ceiling is enforced before the first provider round."""
    budget = FunctionCallBudget(limit=0)
    factory_called = False

    def factory() -> list[dict]:
        nonlocal factory_called
        factory_called = True
        return [{"spec": {"name": "do_work"}}]

    assert _budgeted_function_tools(budget, factory) == []
    assert factory_called is False
    with pytest.raises(HomeAssistantError, match="Function call limit of 0 reached"):
        budget.claim("do_work")


def test_tool_loop_exhaustion_fails_explicitly() -> None:
    """Outstanding tool results after the hard loop are not returned as success."""
    chat_log = SimpleNamespace(unresponded_tool_results=[object(), object()])

    with pytest.raises(
        HomeAssistantError,
        match=r"safety limit of 20 requests with 2 unresolved tool result\(s\)",
    ):
        _assert_tool_loop_completed(chat_log, 20)


def test_completed_tool_loop_is_accepted() -> None:
    """A normal final model response remains unaffected by the exhaustion guard."""
    chat_log = SimpleNamespace(unresponded_tool_results=[])

    _assert_tool_loop_completed(chat_log, 20)
