"""Residual coverage for Function Tool execution budgets."""

from __future__ import annotations

import pytest

from homeassistant.exceptions import HomeAssistantError

from custom_components.extended_openai_conversation_responses.function_call_budget import (
    FunctionCallBudget,
)


def test_negative_limit_is_unlimited_and_reports_no_remaining_slots() -> None:
    """Negative limits stay unlimited even after executions are claimed."""
    budget = FunctionCallBudget(limit=-1)

    assert budget.remaining is None
    assert budget.exhausted is False

    budget.claim_many(("first_tool", "second_tool"))

    assert budget.used == 2
    assert budget.remaining is None
    assert budget.exhausted is False


def test_empty_batch_is_a_noop_even_when_budget_is_exhausted() -> None:
    """An empty reservation must not fail or mutate an exhausted budget."""
    budget = FunctionCallBudget(limit=0)

    assert budget.exhausted is True

    budget.claim_many(())

    assert budget.used == 0
    assert budget.remaining == 0


def test_batch_overflow_is_atomic_and_identifies_first_refused_tool() -> None:
    """A rejected parallel batch must not consume any partial reservation."""
    budget = FunctionCallBudget(limit=2, used=1)

    with pytest.raises(
        HomeAssistantError,
        match=r"Function call limit of 2 reached; refusing to execute additional tool `second_tool`",
    ):
        budget.claim_many(("first_tool", "second_tool"))

    assert budget.used == 1
    assert budget.remaining == 1
    assert budget.exhausted is False
