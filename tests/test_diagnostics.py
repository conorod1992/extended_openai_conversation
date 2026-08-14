"""Tests for non-sensitive integration diagnostics."""

from custom_components.extended_openai_conversation_responses.const import (
    DEFAULT_CONF_FUNCTION_TOOLS,
)
from custom_components.extended_openai_conversation_responses.diagnostics import (
    _configured_function_tools,
)


async def test_missing_function_config_uses_execution_default(hass) -> None:
    """Diagnostics must report the tools execution actually falls back to."""
    assert _configured_function_tools({}) == DEFAULT_CONF_FUNCTION_TOOLS


def test_explicit_empty_function_config_remains_empty() -> None:
    """An explicit empty YAML list must not be replaced by defaults."""
    assert _configured_function_tools({"functions": "[]"}) == []
