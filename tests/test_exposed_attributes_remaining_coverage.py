"""Focused regression coverage for remaining exposed-attribute branches."""

from __future__ import annotations

from typing import Any

import pytest

from custom_components.extended_openai_conversation_responses import (
    conversation,
    exposed_attributes as ea,
    management_ui,
    prompt,
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


def test_installed_template_renderer_dispatches_default_and_custom_templates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The runtime wrapper preserves both maintained and custom template paths."""
    calls: list[tuple[str, list[dict[str, Any]]]] = []

    def original_template_renderer(
        _hass: Any,
        raw: str,
        *,
        exposed_entities: list[dict[str, Any]],
        current_device_id: str | None,
        user_input: Any,
        skills: list[Any],
    ) -> str:
        calls.append((raw, exposed_entities))
        return "custom-rendered"

    # Register every attribute mutated by installation with monkeypatch so teardown
    # restores the module state even though installation assigns them directly.
    monkeypatch.setattr(ea, "_INSTALLED", False)
    monkeypatch.setattr(
        prompt,
        "_default_exposed_entities_context",
        prompt._default_exposed_entities_context,
    )
    monkeypatch.setattr(prompt, "_render_template", original_template_renderer)
    monkeypatch.setattr(prompt, "render_effective_prompt", prompt.render_effective_prompt)
    monkeypatch.setattr(
        conversation,
        "render_effective_prompt",
        conversation.render_effective_prompt,
    )
    monkeypatch.setattr(
        management_ui,
        "render_effective_prompt",
        management_ui.render_effective_prompt,
    )
    monkeypatch.setattr(
        management_ui,
        "async_management_command",
        management_ui.async_management_command,
    )

    ea.install_exposed_attribute_runtime()

    legacy = prompt._render_template(
        object(),
        ea.DEFAULT_EXPOSED_ENTITIES_CONTEXT_TEMPLATE,
        exposed_entities=[],
        current_device_id=None,
        user_input=None,
        skills=[],
    )
    assert legacy.startswith("## Available Devices\n```csv\n")
    assert calls == []

    custom = prompt._render_template(
        object(),
        "{{ custom }}",
        exposed_entities=[{"entity_id": "light.kitchen"}],
        current_device_id=None,
        user_input="hello",
        skills=[],
    )
    assert custom == "custom-rendered"
    assert calls == [("{{ custom }}", [{"entity_id": "light.kitchen"}])]
