"""Focused regression coverage for remaining exposed-attribute branches."""

from __future__ import annotations

from typing import Any

import pytest

from custom_components.extended_openai_conversation_responses import (
    exposed_attributes as ea,
)


def test_normalizer_omits_exposed_attributes_when_defaults_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Optional exposed-attribute config stays absent when defaults are disabled."""

    def original(
        data: Any, *, apply_defaults: bool, reject_unknown: bool
    ) -> dict[str, Any]:
        assert data == {}
        assert apply_defaults is False
        assert reject_unknown is True
        return {"base": True}

    monkeypatch.setattr(ea, "_ORIGINAL_NORMALIZE_AGENT_CONFIG", original)

    assert ea._normalize_agent_config_with_exposed_attributes(
        {}, apply_defaults=False
    ) == {"base": True}


def test_register_agent_config_contract_is_idempotent() -> None:
    """Re-registering an already installed config field is a no-op."""
    defaults = ea.agent_config.AGENT_CONFIG_DEFAULTS
    fields = ea.agent_config.AGENT_CONFIG_FIELDS
    normalizer = ea.agent_config.normalize_agent_config

    ea._register_agent_config_contract()

    assert ea.agent_config.AGENT_CONFIG_DEFAULTS is defaults
    assert ea.agent_config.AGENT_CONFIG_FIELDS is fields
    assert ea.agent_config.normalize_agent_config is normalizer


def test_legacy_renderer_without_selected_attributes_omits_column(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Legacy prompt rows retain their pre-attribute CSV shape when unused."""
    monkeypatch.setattr(ea, "resolve_area_id", lambda _hass, _entity_id: "kitchen")

    rendered = ea._render_legacy_default(
        object(),
        [
            {
                "entity_id": "light.kitchen",
                "name": "Kitchen",
                "state": "on",
                "aliases": ["Main light"],
                "attributes": {"brightness": 123},
            }
        ],
        include_attributes=False,
    )

    header = rendered.splitlines()[2]
    row = rendered.splitlines()[3]
    assert header == "entity_id,name,state,area_id,aliases"
    assert row == "light.kitchen,Kitchen,on,kitchen,Main light"
    assert "brightness" not in rendered
