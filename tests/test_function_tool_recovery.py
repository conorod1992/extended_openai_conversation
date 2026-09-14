"""Focused tests for Function Tool recovery helpers."""

from __future__ import annotations

import pytest

from custom_components.extended_openai_conversation_responses.function_tool_recovery import (
    provider_argument_text,
)


@pytest.mark.parametrize(
    "arguments",
    [
        pytest.param({}, id="mapping"),
        pytest.param(None, id="none"),
        pytest.param("ordinary arguments", id="string"),
    ],
)
def test_provider_argument_text_rejects_ordinary_arguments(arguments: object) -> None:
    """Ordinary argument values are not malformed provider input."""
    with pytest.raises(
        TypeError, match="^arguments are not malformed provider input$"
    ):
        provider_argument_text(arguments)
