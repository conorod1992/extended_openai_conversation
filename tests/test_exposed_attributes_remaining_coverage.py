"""Focused regression coverage for remaining exposed-attribute branches."""

from __future__ import annotations

from typing import Any

import pytest

from custom_components.extended_openai_conversation_responses import (
    exposed_attributes as ea,
)






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
