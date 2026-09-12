"""Focused branch coverage for config-flow helpers and lifecycle handlers."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from openai import APIConnectionError
import pytest

from custom_components.extended_openai_conversation_responses import config_flow
from custom_components.extended_openai_conversation_responses.config_flow import (
    ExtendedOpenAIAITaskSubentryFlowHandler,
    ExtendedOpenAIConversationConfigFlow,
    ExtendedOpenAIOptionsFlow,
    ExtendedOpenAISubentryFlowHandler,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_ADVANCED_OPTIONS,
    CONF_API_MODE,
    CONF_API_PROVIDER,
    CONF_API_VERSION,
    CONF_BASE_URL,
    CONF_CHAT_MODEL,
    CONF_MAX_TOKENS,
    CONF_ORGANIZATION,
    CONF_REASONING_EFFORT,
    CONF_SERVICE_TIER,
    CONF_SHORTEN_TOOL_CALL_ID,
    CONF_SKILLS,
    CONF_SKIP_AUTHENTICATION,
    CONF_TEMPERATURE,
    CONF_TOP_P,
    CONF_WEB_SEARCH,
    DEFAULT_AI_TASK_NAME,
    DEFAULT_AI_TASK_OPTIONS,
    DEFAULT_API_MODE,
    DEFAULT_CHAT_MODEL,
    DEFAULT_CONF_BASE_URL,
    DEFAULT_CONVERSATION_NAME,
    DEFAULT_NAME,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_API_KEY, CONF_NAME
from homeassistant.exceptions import HomeAssistantError


def _sync_result(*_args, **kwargs):
    return kwargs


async def test_options_flow_covers_menu_guidance_and_agent_test() -> None:
    """Exercise every options-flow destination and its return navigation."""
    conversation = SimpleNamespace(
        subentry_id="agent-1", title="Assistant", subentry_type="conversation"
    )
    other = SimpleNamespace(
        subentry_id="task-1", title="AI Task", subentry_type="ai_task_data"
    )
    report = SimpleNamespace(as_text=MagicMock(return_value="All checks passed"))
    flow = SimpleNamespace(
        hass=MagicMock(),
        config_entry=SimpleNamespace(
            subentries={"agent-1": conversation, "task-1": other}
        ),
        _test_report="",
        _agent_options=lambda: ExtendedOpenAIOptionsFlow._agent_options(flow),
        async_show_menu=MagicMock(side_effect=_sync_result),
        async_show_form=MagicMock(side_effect=_sync_result),
    )

    async def step_init(user_input=None):
        return await ExtendedOpenAIOptionsFlow.async_step_init(flow, user_input)

    async def step_test_result(user_input=None):
        return await ExtendedOpenAIOptionsFlow.async_step_test_result(flow, user_input)

    flow.async_step_init = AsyncMock(side_effect=step_init)
    flow.async_step_test_result = AsyncMock(side_effect=step_test_result)

    assert ExtendedOpenAIOptionsFlow._agent_options(flow) == [
        {"value": "agent-1", "label": "Assistant"}
    ]
    menu = await ExtendedOpenAIOptionsFlow.async_step_init(flow)
    assert menu["menu_options"] == [
        "test_agent",
        "manage_memory",
        "manage_knowledge",
    ]

    for method, step_id, default in (
        (
            ExtendedOpenAIOptionsFlow.async_step_manage_memory,
            "manage_memory",
            "/extended-openai-memory",
        ),
        (
            ExtendedOpenAIOptionsFlow.async_step_manage_knowledge,
            "manage_knowledge",
            "/extended-openai-knowledge",
        ),
    ):
        shown = await method(flow)
        assert shown["step_id"] == step_id
        assert shown["data_schema"]({})["panel_path"] == default
        assert await method(flow, {"panel_path": default}) == menu

    selected = await ExtendedOpenAIOptionsFlow.async_step_test_agent(flow)
    assert selected["step_id"] == "test_agent"
    with patch.object(config_flow, "async_test_agent", AsyncMock(return_value=report)):
        result = await ExtendedOpenAIOptionsFlow.async_step_test_agent(
            flow, {"agent_id": "agent-1"}
        )
    assert result["step_id"] == "test_result"
    assert flow._test_report == "All checks passed"
    assert result["data_schema"]({})["report"] == "All checks passed"
    assert await ExtendedOpenAIOptionsFlow.async_step_test_result(flow, {}) == menu


async def test_validate_input_normalizes_openai_and_forwards_provider_settings() -> (
    None
):
    """Canonical OpenAI URLs are omitted while custom provider settings are forwarded."""
    hass = MagicMock()
    authenticate = AsyncMock()
    canonical = {
        CONF_API_KEY: "sk-openai",
        CONF_BASE_URL: DEFAULT_CONF_BASE_URL,
        CONF_API_PROVIDER: "openai",
    }
    custom = {
        CONF_API_KEY: "sk-custom",
        CONF_BASE_URL: "https://provider.example/v1",
        CONF_API_VERSION: "2026-09-01",
        CONF_ORGANIZATION: "org-1",
        CONF_SKIP_AUTHENTICATION: True,
        CONF_API_PROVIDER: "custom",
    }

    with patch.object(config_flow, "get_authenticated_client", authenticate):
        await config_flow.validate_input(hass, canonical)
        await config_flow.validate_input(hass, custom)

    assert CONF_BASE_URL not in canonical
    assert authenticate.await_args_list[0].kwargs["base_url"] is None
    assert authenticate.await_args_list[0].kwargs["skip_authentication"] is False
    assert authenticate.await_args_list[1].kwargs == {
        "hass": hass,
        "api_key": "sk-custom",
        "base_url": "https://provider.example/v1",
        "api_version": "2026-09-01",
        "organization": "org-1",
        "api_provider": "custom",
        "skip_authentication": True,
    }

    with pytest.raises(HomeAssistantError, match="custom base URL"):
        await config_flow.validate_input(
            hass,
            {
                CONF_API_KEY: "sk-azure",
                CONF_API_PROVIDER: "azure",
            },
        )


async def test_config_flow_success_errors_reauth_and_type_registration() -> None:
    """Cover direct config-flow outcomes, including defensive reauthentication paths."""
    flow = SimpleNamespace(
        hass=MagicMock(),
        context={"entry_id": "entry-1"},
        _reauth_entry=None,
        async_show_form=MagicMock(side_effect=_sync_result),
        async_create_entry=MagicMock(side_effect=_sync_result),
        async_abort=MagicMock(side_effect=_sync_result),
        async_update_reload_and_abort=MagicMock(side_effect=_sync_result),
    )

    async def step_reauth_confirm(user_input=None):
        return await ExtendedOpenAIConversationConfigFlow.async_step_reauth_confirm(
            flow, user_input
        )

    flow.async_step_reauth_confirm = AsyncMock(side_effect=step_reauth_confirm)

    shown = await ExtendedOpenAIConversationConfigFlow.async_step_user(flow)
    assert shown["step_id"] == "user"

    submitted = {CONF_NAME: "Assistant", CONF_API_KEY: "sk-test"}
    with patch.object(config_flow, "validate_input", AsyncMock()):
        created = await ExtendedOpenAIConversationConfigFlow.async_step_user(
            flow, submitted
        )
    assert created["title"] == "Assistant"
    assert [item["subentry_type"] for item in created["subentries"]] == [
        "conversation",
        "ai_task_data",
    ]
    with patch.object(config_flow, "validate_input", AsyncMock()):
        created = await ExtendedOpenAIConversationConfigFlow.async_step_user(
            flow, {CONF_API_KEY: "sk-default-title"}
        )
    assert created["title"] == DEFAULT_NAME

    with patch.object(
        config_flow,
        "validate_input",
        AsyncMock(side_effect=APIConnectionError(request=MagicMock())),
    ):
        failed = await ExtendedOpenAIConversationConfigFlow.async_step_user(
            flow, {CONF_API_KEY: "sk-test"}
        )
    assert failed["errors"] == {"base": "cannot_connect"}

    with patch.object(
        config_flow, "validate_input", AsyncMock(side_effect=RuntimeError("boom"))
    ):
        failed = await ExtendedOpenAIConversationConfigFlow.async_step_user(
            flow, {CONF_API_KEY: "sk-test"}
        )
    assert failed["errors"] == {"base": "unknown"}

    missing = await ExtendedOpenAIConversationConfigFlow.async_step_reauth_confirm(flow)
    assert missing == {"reason": "reauth_failed"}

    entry = SimpleNamespace(data={CONF_API_KEY: "old", CONF_ORGANIZATION: "org"})
    flow.hass.config_entries.async_get_entry.return_value = entry
    reauth_form = await ExtendedOpenAIConversationConfigFlow.async_step_reauth(flow, {})
    assert flow._reauth_entry is entry
    assert reauth_form["step_id"] == "reauth_confirm"

    with patch.object(
        config_flow,
        "validate_input",
        AsyncMock(side_effect=APIConnectionError(request=MagicMock())),
    ):
        failed = await ExtendedOpenAIConversationConfigFlow.async_step_reauth_confirm(
            flow, {CONF_API_KEY: "new"}
        )
    assert failed["errors"] == {"base": "cannot_connect"}

    with patch.object(
        config_flow, "validate_input", AsyncMock(side_effect=RuntimeError("boom"))
    ):
        failed = await ExtendedOpenAIConversationConfigFlow.async_step_reauth_confirm(
            flow, {CONF_API_KEY: "new"}
        )
    assert failed["errors"] == {"base": "unknown"}

    async def mutate_entry(_hass, _updated):
        entry.data = {CONF_API_KEY: "old", CONF_ORGANIZATION: "changed"}

    with patch.object(config_flow, "validate_input", mutate_entry):
        failed = await ExtendedOpenAIConversationConfigFlow.async_step_reauth_confirm(
            flow, {CONF_API_KEY: "new"}
        )
    assert failed["errors"] == {"base": "config_changed"}

    entry.data = {CONF_API_KEY: "old", CONF_ORGANIZATION: "org"}
    with patch.object(config_flow, "validate_input", AsyncMock()):
        updated = await ExtendedOpenAIConversationConfigFlow.async_step_reauth_confirm(
            flow, {CONF_API_KEY: "new"}
        )
    assert updated["data"] == {CONF_API_KEY: "new", CONF_ORGANIZATION: "org"}

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


async def test_conversation_subentry_handler_covers_all_lifecycle_paths() -> None:
    """Cover creation, panel-only reconfigure, unavailable parent, and skill loading."""
    assert (
        ExtendedOpenAISubentryFlowHandler._is_new.fget(SimpleNamespace(source="user"))
        is True
    )
    assert (
        ExtendedOpenAISubentryFlowHandler._is_new.fget(
            SimpleNamespace(source="reconfigure")
        )
        is False
    )
    defaults = {"prompt": "default"}
    flow = SimpleNamespace(
        options={}, async_step_init=AsyncMock(return_value={"step": "init"})
    )
    with patch.object(config_flow, "agent_config_defaults", return_value=defaults):
        assert await ExtendedOpenAISubentryFlowHandler.async_step_user(flow) == {
            "step": "init"
        }
    assert flow.options == defaults

    existing = SimpleNamespace(data={"legacy": "value"})
    flow._get_reconfigure_subentry = MagicMock(return_value=existing)
    assert await ExtendedOpenAISubentryFlowHandler.async_step_reconfigure(flow) == {
        "step": "init"
    }
    assert flow.options == existing.data

    handler = SimpleNamespace(
        options={"prompt": "default"},
        _is_new=True,
        _get_entry=MagicMock(
            return_value=SimpleNamespace(state=ConfigEntryState.NOT_LOADED)
        ),
        async_abort=MagicMock(side_effect=_sync_result),
        async_create_entry=MagicMock(side_effect=_sync_result),
        async_show_form=MagicMock(side_effect=_sync_result),
        _async_get_skills=AsyncMock(return_value=[{"name": "weather"}]),
    )
    aborted = await ExtendedOpenAISubentryFlowHandler.async_step_init(handler)
    assert aborted == {"reason": "entry_not_loaded"}

    handler._get_entry.return_value.state = ConfigEntryState.LOADED
    shown = await ExtendedOpenAISubentryFlowHandler.async_step_init(handler)
    assert shown["step_id"] == "init"
    with patch.object(config_flow, "normalize_agent_config", side_effect=dict):
        created = await ExtendedOpenAISubentryFlowHandler.async_step_init(
            handler, {CONF_NAME: "  "}
        )
    assert created["title"] == DEFAULT_CONVERSATION_NAME
    assert created["data"][CONF_SKILLS] == ["weather"]
    with patch.object(config_flow, "normalize_agent_config", side_effect=dict):
        created = await ExtendedOpenAISubentryFlowHandler.async_step_init(
            handler, {CONF_NAME: "Named Agent"}
        )
    assert created["title"] == "Named Agent"

    handler._is_new = False
    shown = await ExtendedOpenAISubentryFlowHandler.async_step_init(handler)
    assert shown["data_schema"]({})["management_panel"].startswith("Open Extended")
    aborted = await ExtendedOpenAISubentryFlowHandler.async_step_init(handler, {})
    assert aborted == {"reason": "agent_configuration_managed_in_panel"}

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


async def test_ai_task_handler_covers_basic_and_advanced_lifecycle_paths() -> None:
    """Exercise AI Task forms, direct saves, advanced saves, and capability pruning."""
    assert (
        ExtendedOpenAIAITaskSubentryFlowHandler._is_new.fget(
            SimpleNamespace(source="user")
        )
        is True
    )
    assert (
        ExtendedOpenAIAITaskSubentryFlowHandler._is_new.fget(
            SimpleNamespace(source="reconfigure")
        )
        is False
    )
    flow = SimpleNamespace(
        options={}, async_step_init=AsyncMock(return_value={"step": "init"})
    )
    assert await ExtendedOpenAIAITaskSubentryFlowHandler.async_step_user(flow) == {
        "step": "init"
    }
    assert flow.options == dict(DEFAULT_AI_TASK_OPTIONS)

    existing = SimpleNamespace(data={CONF_CHAT_MODEL: "saved-model"})
    flow._get_reconfigure_subentry = MagicMock(return_value=existing)
    assert await ExtendedOpenAIAITaskSubentryFlowHandler.async_step_reconfigure(
        flow
    ) == {"step": "init"}
    assert flow.options == existing.data

    handler = SimpleNamespace(
        options=dict(DEFAULT_AI_TASK_OPTIONS),
        _temp_data=None,
        _is_new=True,
        _get_entry=MagicMock(
            return_value=SimpleNamespace(state=ConfigEntryState.NOT_LOADED)
        ),
        _get_reconfigure_subentry=MagicMock(return_value=existing),
        async_abort=MagicMock(side_effect=_sync_result),
        async_create_entry=MagicMock(side_effect=_sync_result),
        async_update_and_abort=MagicMock(side_effect=_sync_result),
        async_show_form=MagicMock(side_effect=_sync_result),
        add_suggested_values_to_schema=MagicMock(
            side_effect=lambda schema, _values: schema
        ),
        async_step_advanced=AsyncMock(return_value={"step_id": "advanced"}),
    )
    assert await ExtendedOpenAIAITaskSubentryFlowHandler.async_step_init(handler) == {
        "reason": "entry_not_loaded"
    }

    handler._get_entry.return_value.state = ConfigEntryState.LOADED
    shown = await ExtendedOpenAIAITaskSubentryFlowHandler.async_step_init(handler)
    assert shown["step_id"] == "init"
    assert CONF_NAME in {str(key) for key in shown["data_schema"].schema}

    advanced_input = {
        CONF_NAME: "Advanced",
        CONF_CHAT_MODEL: DEFAULT_CHAT_MODEL,
        CONF_ADVANCED_OPTIONS: True,
    }
    assert await ExtendedOpenAIAITaskSubentryFlowHandler.async_step_init(
        handler, advanced_input
    ) == {"step_id": "advanced"}
    assert handler._temp_data is advanced_input

    basic_input = {
        CONF_NAME: "Basic",
        CONF_CHAT_MODEL: DEFAULT_CHAT_MODEL,
        CONF_API_MODE: DEFAULT_API_MODE,
        CONF_MAX_TOKENS: 100,
        CONF_WEB_SEARCH: False,
        CONF_ADVANCED_OPTIONS: False,
    }
    created = await ExtendedOpenAIAITaskSubentryFlowHandler.async_step_init(
        handler, basic_input
    )
    assert created["title"] == "Basic"
    assert CONF_NAME not in created["data"]
    created = await ExtendedOpenAIAITaskSubentryFlowHandler.async_step_init(
        handler,
        {
            CONF_CHAT_MODEL: DEFAULT_CHAT_MODEL,
            CONF_ADVANCED_OPTIONS: False,
        },
    )
    assert created["title"] == DEFAULT_AI_TASK_NAME

    handler._is_new = False
    shown = await ExtendedOpenAIAITaskSubentryFlowHandler.async_step_init(handler)
    assert CONF_NAME not in {str(key) for key in shown["data_schema"].schema}
    updated = await ExtendedOpenAIAITaskSubentryFlowHandler.async_step_init(
        handler,
        {
            CONF_CHAT_MODEL: DEFAULT_CHAT_MODEL,
            CONF_ADVANCED_OPTIONS: False,
        },
    )
    assert updated["data"][CONF_CHAT_MODEL] == DEFAULT_CHAT_MODEL

    handler._is_new = True
    handler._temp_data = {
        CONF_NAME: "Reasoning Task",
        CONF_CHAT_MODEL: "reasoning-model",
        CONF_TEMPERATURE: 0.5,
        CONF_TOP_P: 0.8,
    }
    handler.async_step_advanced = None
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
