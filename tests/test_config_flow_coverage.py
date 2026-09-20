"""Focused unit coverage for config-flow behavior not owned by Real HA acceptance."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.extended_openai_conversation_responses import config_flow
from custom_components.extended_openai_conversation_responses.config_flow import (
    ExtendedOpenAIAITaskSubentryFlowHandler,
    ExtendedOpenAIConversationConfigFlow,
    ExtendedOpenAIOptionsFlow,
    ExtendedOpenAISubentryFlowHandler,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_API_PROVIDER,
    CONF_API_VERSION,
    CONF_BASE_URL,
    CONF_CHAT_MODEL,
    CONF_ORGANIZATION,
    CONF_REASONING_EFFORT,
    CONF_SERVICE_TIER,
    CONF_SHORTEN_TOOL_CALL_ID,
    CONF_SKILLS,
    CONF_SKIP_AUTHENTICATION,
    CONF_TEMPERATURE,
    CONF_TOP_P,
    DEFAULT_CONVERSATION_NAME,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_API_KEY, CONF_NAME


def _sync_result(*_args, **kwargs):
    return kwargs


def test_options_flow_agent_choices_only_include_conversation_subentries() -> None:
    """Keep the internal agent-option filter unit-owned.

    Real HA owns the options-menu navigation and agent-test round trip.
    """
    conversation = SimpleNamespace(
        subentry_id="agent-1", title="Assistant", subentry_type="conversation"
    )
    other = SimpleNamespace(
        subentry_id="task-1", title="AI Task", subentry_type="ai_task_data"
    )
    flow = SimpleNamespace(
        config_entry=SimpleNamespace(
            subentries={"agent-1": conversation, "task-1": other}
        )
    )

    assert ExtendedOpenAIOptionsFlow._agent_options(flow) == [
        {"value": "agent-1", "label": "Assistant"}
    ]


async def test_validate_input_forwards_custom_provider_settings() -> None:
    """Keep exact provider-client argument forwarding unit-owned.

    Canonical OpenAI defaults and Azure endpoint validation are covered through
    Home Assistant's real config-flow manager in tests_real_ha.
    """
    hass = MagicMock()
    authenticate = AsyncMock()
    custom = {
        CONF_API_KEY: "sk-custom",
        CONF_BASE_URL: "https://provider.example/v1",
        CONF_API_VERSION: "2026-09-01",
        CONF_ORGANIZATION: "org-1",
        CONF_SKIP_AUTHENTICATION: True,
        CONF_API_PROVIDER: "custom",
    }

    with patch.object(config_flow, "get_authenticated_client", authenticate):
        await config_flow.validate_input(hass, custom)

    authenticate.assert_awaited_once_with(
        hass=hass,
        api_key="sk-custom",
        base_url="https://provider.example/v1",
        api_version="2026-09-01",
        organization="org-1",
        api_provider="custom",
        skip_authentication=True,
    )


def test_config_flow_registers_options_and_subentry_types() -> None:
    """Retain structural type registration without replaying lifecycle flows."""
    entry = SimpleNamespace()

    assert isinstance(
        ExtendedOpenAIConversationConfigFlow.async_get_options_flow(entry),
        ExtendedOpenAIOptionsFlow,
    )
    assert ExtendedOpenAIConversationConfigFlow.async_get_supported_subentry_types(
        entry
    ) == {
        "conversation": ExtendedOpenAISubentryFlowHandler,
        "ai_task_data": ExtendedOpenAIAITaskSubentryFlowHandler,
    }


async def test_conversation_subentry_unique_defaults_and_skill_projection() -> None:
    """Retain helper behavior not asserted by Real HA config-flow acceptance."""
    defaults = {"prompt": "default"}
    flow = SimpleNamespace(
        options={}, async_step_init=AsyncMock(return_value={"step": "init"})
    )
    with patch.object(config_flow, "agent_config_defaults", return_value=defaults):
        assert await ExtendedOpenAISubentryFlowHandler.async_step_user(flow) == {
            "step": "init"
        }
    assert flow.options == defaults

    handler = SimpleNamespace(
        options={"prompt": "default"},
        _is_new=True,
        _get_entry=MagicMock(
            return_value=SimpleNamespace(state=ConfigEntryState.LOADED)
        ),
        async_create_entry=MagicMock(side_effect=_sync_result),
        async_show_form=MagicMock(side_effect=_sync_result),
        _async_get_skills=AsyncMock(return_value=[{"name": "weather"}]),
    )
    with patch.object(config_flow, "normalize_agent_config", side_effect=dict):
        created = await ExtendedOpenAISubentryFlowHandler.async_step_init(
            handler, {CONF_NAME: "  "}
        )
    assert created["title"] == DEFAULT_CONVERSATION_NAME
    assert created["data"][CONF_SKILLS] == ["weather"]

    skills = [SimpleNamespace(name="weather", description="Forecasts")]
    manager = SimpleNamespace(get_all_skills=lambda: skills)
    skill_flow = SimpleNamespace(hass=MagicMock())
    with patch.object(
        config_flow.SkillManager,
        "async_get_instance",
        AsyncMock(return_value=manager),
    ):
        assert await ExtendedOpenAISubentryFlowHandler._async_get_skills(
            skill_flow
        ) == [{"name": "weather", "description": "Forecasts"}]


async def test_ai_task_advanced_schema_preserves_capability_pruning() -> None:
    """Keep fine-grained capability/schema branches in the ordinary suite.

    Real HA owns basic/advanced creation, reconfiguration, unloaded-parent,
    default-title, and representative reasoning-model lifecycle behavior.
    """
    handler = SimpleNamespace(
        options={},
        _temp_data={
            CONF_NAME: "Reasoning Task",
            CONF_CHAT_MODEL: "reasoning-model",
            CONF_TEMPERATURE: 0.5,
            CONF_TOP_P: 0.8,
        },
        _is_new=True,
        async_create_entry=MagicMock(side_effect=_sync_result),
        async_update_and_abort=MagicMock(side_effect=_sync_result),
        async_show_form=MagicMock(side_effect=_sync_result),
        add_suggested_values_to_schema=MagicMock(
            side_effect=lambda schema, _values: schema
        ),
    )
    metadata = {
        "reasoning": {"supported": True, "efforts": ["low", "high"]},
        "service_tier": True,
    }

    def allowed(_model, parameter, effort):
        return parameter == CONF_TOP_P and effort == "high"

    with (
        patch.object(config_flow, "get_model_capabilities", return_value=metadata),
        patch.object(config_flow, "recommended_reasoning_effort", return_value="low"),
        patch.object(
            config_flow, "_ai_task_display_reasoning_effort", return_value="high"
        ),
        patch.object(config_flow, "parameter_is_allowed", side_effect=allowed),
    ):
        shown = await ExtendedOpenAIAITaskSubentryFlowHandler.async_step_advanced(
            handler
        )
        keys = {str(key) for key in shown["data_schema"].schema}
        assert CONF_TOP_P in keys
        assert CONF_TEMPERATURE not in keys
        assert CONF_REASONING_EFFORT in keys
        assert CONF_SERVICE_TIER in keys
        assert CONF_SHORTEN_TOOL_CALL_ID in keys

        created = await ExtendedOpenAIAITaskSubentryFlowHandler.async_step_advanced(
            handler,
            {
                CONF_REASONING_EFFORT: "high",
                CONF_TEMPERATURE: 0.2,
                CONF_TOP_P: 0.7,
            },
        )
    assert created["title"] == "Reasoning Task"
    assert CONF_NAME not in created["data"]
    assert CONF_TEMPERATURE not in created["data"]
    assert created["data"][CONF_TOP_P] == 0.7

    with (
        patch.object(config_flow, "get_model_capabilities", return_value=metadata),
        patch.object(config_flow, "recommended_reasoning_effort", return_value="low"),
        patch.object(
            config_flow, "_ai_task_display_reasoning_effort", return_value=None
        ),
        patch.object(config_flow, "parameter_is_allowed", return_value=True),
    ):
        shown = await ExtendedOpenAIAITaskSubentryFlowHandler.async_step_advanced(
            handler
        )
    schema = shown["data_schema"]
    keys = {str(key) for key in schema.schema}
    assert {CONF_TOP_P, CONF_TEMPERATURE, CONF_REASONING_EFFORT} <= keys
    assert schema({})[CONF_REASONING_EFFORT] == "low"

    handler._is_new = False
    handler.options = {CONF_CHAT_MODEL: "reasoning-model"}
    handler._temp_data = {CONF_CHAT_MODEL: "reasoning-model"}
    metadata = {"reasoning": {"supported": False, "efforts": []}, "service_tier": False}
    with (
        patch.object(config_flow, "get_model_capabilities", return_value=metadata),
        patch.object(config_flow, "recommended_reasoning_effort", return_value=None),
        patch.object(
            config_flow, "_ai_task_display_reasoning_effort", return_value=None
        ),
        patch.object(config_flow, "parameter_is_allowed", return_value=False),
    ):
        shown = await ExtendedOpenAIAITaskSubentryFlowHandler.async_step_advanced(
            handler
        )
        assert {str(key) for key in shown["data_schema"].schema} == {
            CONF_SHORTEN_TOOL_CALL_ID
        }
        updated = await ExtendedOpenAIAITaskSubentryFlowHandler.async_step_advanced(
            handler, {CONF_TEMPERATURE: 0.2, CONF_TOP_P: 0.7}
        )
    assert CONF_TEMPERATURE not in updated["data"]
    assert CONF_TOP_P not in updated["data"]
