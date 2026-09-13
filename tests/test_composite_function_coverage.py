"""Focused coverage for composite function validation."""

from __future__ import annotations

import pytest
import voluptuous as vol

from custom_components.extended_openai_conversation_responses.functions.composite import (
    CompositeFunction,
)


def test_function_schema_rejects_non_mapping_config() -> None:
    """Composite sequence entries must be function configuration mappings."""
    composite = CompositeFunction()

    with pytest.raises(vol.Invalid, match="expected dictionary"):
        composite.function_schema("not-a-function-config")
