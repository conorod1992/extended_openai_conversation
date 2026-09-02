"""Tests for shared model-facing capability gating."""

from types import SimpleNamespace

from custom_components.extended_openai_conversation_responses.agent_config import (
    agent_config_defaults,
)
from custom_components.extended_openai_conversation_responses.capabilities import (
    persistent_memory_scope_available,
    resolve_effective_capabilities,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_CURRENT_DATETIME_ENABLED,
    CONF_EXPOSED_ENTITIES_ENABLED,
    CONF_MEMORY_MODE,
    CONF_PROMPT,
    CONF_SHARED_MEMORY_MODE,
    CONF_VOICE_SCOPE_POLICY,
    MEMORY_MODE_AUTOMATIC,
    SHARED_MEMORY_DISABLED,
    SHARED_MEMORY_EXPLICIT,
    VOICE_POLICY_SHARED,
)
from custom_components.extended_openai_conversation_responses.memory import (
    MEMORY_TOOL_NAMES,
)
from custom_components.extended_openai_conversation_responses.prompt import (
    render_effective_prompt,
)
from custom_components.extended_openai_conversation_responses.request import (
    assemble_integration_function_tools,
)
from custom_components.extended_openai_conversation_responses.scope import (
    shared_scope,
    unretained_scope,
    user_scope,
)
from homeassistant.core import Context


def _options(shared_memory_mode: str) -> dict:
    options = agent_config_defaults()
    options.update(
        {
            CONF_PROMPT: "BASE",
            CONF_MEMORY_MODE: MEMORY_MODE_AUTOMATIC,
            CONF_VOICE_SCOPE_POLICY: VOICE_POLICY_SHARED,
            CONF_SHARED_MEMORY_MODE: shared_memory_mode,
            CONF_CURRENT_DATETIME_ENABLED: False,
            CONF_EXPOSED_ENTITIES_ENABLED: False,
        }
    )
    return options


def _voice_input() -> SimpleNamespace:
    return SimpleNamespace(
        context=Context(),
        satellite_id="voice-satellite",
        device_id=None,
    )


def _tool_names(options: dict, memory_scope_available: bool) -> set[str]:
    return {
        tool["spec"]["name"]
        for tool in assemble_integration_function_tools(
            options,
            set(),
            memory_scope_available=memory_scope_available,
            temporary_scope_available=False,
            knowledge_available=False,
            archive_available=False,
        )
    }


def test_memory_scope_availability_respects_retention_and_shared_mode() -> None:
    """Scope eligibility is deterministic before prompt/tool capability gating."""
    disabled = _options(SHARED_MEMORY_DISABLED)
    explicit = _options(SHARED_MEMORY_EXPLICIT)

    assert persistent_memory_scope_available(
        disabled, user_scope("admin", source="test")
    )
    assert not persistent_memory_scope_available(disabled, unretained_scope())
    assert not persistent_memory_scope_available(
        disabled, shared_scope(source="test")
    )
    assert persistent_memory_scope_available(explicit, shared_scope(source="test"))


def test_effective_memory_capability_requires_enabled_usable_scope() -> None:
    """Configuration alone is insufficient when the resolved scope cannot use memory."""
    options = _options(SHARED_MEMORY_DISABLED)

    assert not resolve_effective_capabilities(
        options, memory_scope_available=False
    ).persistent_memory
    assert resolve_effective_capabilities(
        options, memory_scope_available=True
    ).persistent_memory


def test_shared_voice_with_memory_disabled_omits_prompt_and_tools(hass) -> None:
    """A shared voice request never advertises persistent memory when unusable."""
    options = _options(SHARED_MEMORY_DISABLED)
    scope_available = persistent_memory_scope_available(
        options, shared_scope(source="shared_voice_policy")
    )

    prompt = render_effective_prompt(
        hass,
        options,
        exposed_entities=[],
        current_device_id=None,
        user_input=_voice_input(),
        skills=[],
    )

    assert "persistent_memory_instructions" not in {
        section.key for section in prompt.sections
    }
    assert not (_tool_names(options, scope_available) & MEMORY_TOOL_NAMES)


def test_shared_voice_with_shared_memory_enabled_keeps_prompt_and_tools(hass) -> None:
    """Enabling household memory keeps instructions and tools available together."""
    options = _options(SHARED_MEMORY_EXPLICIT)
    scope_available = persistent_memory_scope_available(
        options, shared_scope(source="shared_voice_policy")
    )

    prompt = render_effective_prompt(
        hass,
        options,
        exposed_entities=[],
        current_device_id=None,
        user_input=_voice_input(),
        skills=[],
    )

    assert "persistent_memory_instructions" in {
        section.key for section in prompt.sections
    }
    assert MEMORY_TOOL_NAMES <= _tool_names(options, scope_available)


def test_explicit_scope_capability_overrides_identityless_preview(hass) -> None:
    """Callers with a resolved scope can pass the exact capability decision."""
    options = _options(SHARED_MEMORY_DISABLED)

    prompt = render_effective_prompt(
        hass,
        options,
        exposed_entities=[],
        current_device_id=None,
        user_input=None,
        skills=[],
        memory_scope_available=False,
    )

    assert "persistent_memory_instructions" not in {
        section.key for section in prompt.sections
    }
