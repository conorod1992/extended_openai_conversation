"""Regression tests for model-facing exposed-entity aliases."""

from custom_components.extended_openai_conversation_responses.helpers import (
    normalize_entity_aliases,
)
from homeassistant.helpers import entity_registry as er


def test_computed_name_sentinel_is_omitted() -> None:
    assert normalize_entity_aliases([er.COMPUTED_NAME]) == []


def test_computed_name_sentinel_keeps_genuine_aliases() -> None:
    assert normalize_entity_aliases(
        [er.COMPUTED_NAME, "all the lights", "all of the lights"]
    ) == ["all the lights", "all of the lights"]


def test_genuine_aliases_are_unchanged() -> None:
    assert normalize_entity_aliases(["bedroom lamp", "reading light"]) == [
        "bedroom lamp",
        "reading light",
    ]
