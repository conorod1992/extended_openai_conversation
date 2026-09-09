"""Config-flow acceptance tests driven through Home Assistant's real flow managers."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from openai import APIConnectionError, AuthenticationError
import pytest

from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
from homeassistant.const import CONF_API_KEY, CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.extended_openai_conversation_responses.agent_config import (
    agent_config_defaults,
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
    CONF_SHORTEN_TOOL_CALL_ID,
    CONF_SKIP_AUTHENTICATION,
    CONF_SKILLS,
    CONF_TEMPERATURE,
    CONF_TOP_P,
    CONF_WEB_SEARCH,
    CONFIG_ENTRY_VERSION,
    DEFAULT_AI_TASK_NAME,
    DEFAULT_AI_TASK_OPTIONS,
    DEFAULT_API_MODE,
    DEFAULT_CHAT_MODEL,
    DEFAULT_CONF_BASE_URL,
    DOMAIN,
)

CONFIG_FLOW_MODULE = (
    "custom_components.extended_openai_conversation_responses.config_flow"
)


def _subentry(subentry_type: str, title: str, data: dict | None = None) -> dict:
    """Return storage-shaped subentry data for MockConfigEntry."""
    return {
        "data": data or {},
        "subentry_type": subentry_type,
        "title": title,
        "unique_id": None,
    }


def _make_entry(
    title: str = "Config Flow Acceptance",
    *,
    data: dict | None = None,
) -> MockConfigEntry:
    """Create a current entry that can load without provider network access."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=title,
        data=data
        or {
            CONF_API_KEY: "sk-config-flow-test",
            CONF_SKIP_AUTHENTICATION: True,
        },
        version=CONFIG_ENTRY_VERSION,
        subentries_data=[
            _subentry("conversation", f"{title} Conversation", agent_config_defaults()),
            _subentry(
                "ai_task_data",
                f"{title} AI Task",
                dict(DEFAULT_AI_TASK_OPTIONS),
            ),
        ],
    )


async def _setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Load an entry through Home Assistant so subentry flows use real lifecycle state."""
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED


def _subentry_id(entry: MockConfigEntry, subentry_type: str) -> str:
    return next(
        subentry.subentry_id
        for subentry in entry.subentries.values()
        if subentry.subentry_type == subentry_type
    )


def _authentication_error() -> AuthenticationError:
    return AuthenticationError(
        "invalid",
        response=httpx.Response(
            401, request=httpx.Request("GET", "https://api.openai.com/v1/models")
        ),
        body=None,
    )


@pytest.mark.asyncio
async def test_user_flow_creates_default_subentries_and_normalizes_openai_url(
    hass: HomeAssistant,
) -> None:
    """Create an entry through HA's flow manager and inspect the resulting ConfigEntry."""
    with patch(
        f"{CONFIG_FLOW_MODULE}.get_authenticated_client",
        new_callable=AsyncMock,
    ) as authenticate:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "user"
        assert not result["errors"]

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_NAME: "Real Flow",
                CONF_API_KEY: "sk-real-flow",
                CONF_BASE_URL: DEFAULT_CONF_BASE_URL,
                CONF_SKIP_AUTHENTICATION: True,
                CONF_API_PROVIDER: "openai",
            },
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Real Flow"
    assert result["data"][CONF_API_KEY] == "sk-real-flow"
    assert result["data"][CONF_SKIP_AUTHENTICATION] is True
    # validate_input deliberately removes the canonical OpenAI URL so a future
    # upstream endpoint change is not frozen into stored configuration.
    assert CONF_BASE_URL not in result["data"]

    entry = result["result"]
    assert entry.version == CONFIG_ENTRY_VERSION
    assert {subentry.subentry_type for subentry in entry.subentries.values()} == {
        "conversation",
        "ai_task_data",
    }
    authenticate.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("first_error", "expected_error"),
    [
        (_authentication_error(), "invalid_auth"),
        (
            APIConnectionError(
                request=httpx.Request("GET", "https://api.openai.com/v1/models")
            ),
            "cannot_connect",
        ),
        (RuntimeError("unexpected validation failure"), "unknown"),
    ],
)
async def test_user_flow_provider_error_can_be_corrected_without_restarting_flow(
    hass: HomeAssistant,
    first_error: Exception,
    expected_error: str,
) -> None:
    """A failed submission stays recoverable and a corrected retry creates the entry."""
    authenticate = AsyncMock(side_effect=[first_error, object()])
    with patch(f"{CONFIG_FLOW_MODULE}.get_authenticated_client", authenticate):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        flow_id = result["flow_id"]
        user_input = {
            CONF_NAME: "Retry Flow",
            CONF_API_KEY: "sk-retry-flow",
            CONF_BASE_URL: DEFAULT_CONF_BASE_URL,
            CONF_SKIP_AUTHENTICATION: True,
            CONF_API_PROVIDER: "openai",
        }

        result = await hass.config_entries.flow.async_configure(flow_id, dict(user_input))
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "user"
        assert result["errors"] == {"base": expected_error}

        result = await hass.config_entries.flow.async_configure(flow_id, dict(user_input))

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_API_KEY] == "sk-retry-flow"
    assert authenticate.await_count == 2


@pytest.mark.asyncio
async def test_reauth_error_retry_preserves_provider_settings(
    hass: HomeAssistant,
) -> None:
    """Drive reauthentication through HA and preserve all non-credential settings."""
    original_data = {
        CONF_API_KEY: "expired",
        CONF_API_PROVIDER: "azure",
        CONF_BASE_URL: "https://example.openai.azure.com",
        CONF_API_VERSION: "2025-01-01-preview",
        CONF_ORGANIZATION: "org-id",
        CONF_SKIP_AUTHENTICATION: False,
    }
    entry = _make_entry(data=original_data)
    entry.add_to_hass(hass)

    authenticate = AsyncMock(side_effect=[_authentication_error(), object()])
    with patch(f"{CONFIG_FLOW_MODULE}.get_authenticated_client", authenticate):
        result = await entry.start_reauth_flow(hass)
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "reauth_confirm"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_API_KEY: "replacement"}
        )
        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": "invalid_auth"}
        assert dict(entry.data) == original_data

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_API_KEY: "replacement"}
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_API_KEY] == "replacement"
    for key in (
        CONF_API_PROVIDER,
        CONF_BASE_URL,
        CONF_API_VERSION,
        CONF_ORGANIZATION,
        CONF_SKIP_AUTHENTICATION,
    ):
        assert entry.data[key] == original_data[key]


@pytest.mark.asyncio
async def test_reauth_does_not_overwrite_concurrent_provider_edit(
    hass: HomeAssistant,
) -> None:
    """A provider-setting edit during credential validation must win over stale reauth."""
    entry = _make_entry(
        data={
            CONF_API_KEY: "expired",
            CONF_API_PROVIDER: "openai",
            CONF_ORGANIZATION: "before",
            CONF_SKIP_AUTHENTICATION: True,
        }
    )
    entry.add_to_hass(hass)

    async def validate_and_change_config(_hass: HomeAssistant, _data: dict) -> None:
        hass.config_entries.async_update_entry(
            entry,
            data={**entry.data, CONF_ORGANIZATION: "changed-concurrently"},
        )
        await hass.async_block_till_done()

    with patch(f"{CONFIG_FLOW_MODULE}.validate_input", validate_and_change_config):
        result = await entry.start_reauth_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_API_KEY: "replacement"}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "config_changed"}
    assert entry.data[CONF_API_KEY] == "expired"
    assert entry.data[CONF_ORGANIZATION] == "changed-concurrently"


@pytest.mark.asyncio
async def test_conversation_subentry_add_uses_real_subentry_manager(
    hass: HomeAssistant,
) -> None:
    """Add a conversation agent through HA and seed currently available skills."""
    entry = _make_entry()
    await _setup_entry(hass, entry)
    existing_ids = set(entry.subentries)

    fake_skill = SimpleNamespace(name="weather", description="Weather skill")
    fake_manager = SimpleNamespace(get_all_skills=lambda: [fake_skill])
    with patch(
        f"{CONFIG_FLOW_MODULE}.SkillManager.async_get_instance",
        AsyncMock(return_value=fake_manager),
    ):
        result = await hass.config_entries.subentries.async_init(
            (entry.entry_id, "conversation"), context={"source": SOURCE_USER}
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "init"

        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"], {CONF_NAME: "Second Agent"}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Second Agent"
    new_ids = set(entry.subentries) - existing_ids
    assert len(new_ids) == 1
    created = entry.subentries[new_ids.pop()]
    assert created.subentry_type == "conversation"
    assert created.data[CONF_SKILLS] == ["weather"]


@pytest.mark.asyncio
async def test_conversation_subentry_flow_aborts_when_parent_not_loaded(
    hass: HomeAssistant,
) -> None:
    """Do not let a lifecycle form create an agent on an inactive parent entry."""
    entry = _make_entry()
    entry.add_to_hass(hass)

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "conversation"), context={"source": SOURCE_USER}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "entry_not_loaded"


@pytest.mark.asyncio
async def test_conversation_subentry_reconfigure_is_panel_only_and_non_destructive(
    hass: HomeAssistant,
) -> None:
    """Opening native reconfigure must tolerate legacy data and never rewrite it."""
    entry = _make_entry()
    conversation_id = _subentry_id(entry, "conversation")
    conversation_subentry = entry.subentries[conversation_id]
    legacy_data = {**conversation_subentry.data, "functions": "legacy-custom-value"}
    # Put deliberately non-normalized legacy/custom data in place before setup. The
    # native reconfigure screen is only a pointer to the management panel and must
    # not validate or rewrite this configuration.
    entry.subentries[conversation_id] = conversation_subentry._replace(data=legacy_data)
    await _setup_entry(hass, entry)

    result = await entry.start_subentry_reconfigure_flow(hass, conversation_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"management_panel": "ignored"}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "agent_configuration_managed_in_panel"
    assert entry.subentries[conversation_id].data == legacy_data


@pytest.mark.asyncio
async def test_ai_task_subentry_add_basic_and_advanced_paths(
    hass: HomeAssistant,
) -> None:
    """Exercise both direct-save and advanced multi-step AI Task creation."""
    entry = _make_entry()
    await _setup_entry(hass, entry)

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "ai_task_data"), context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Basic Task",
            CONF_CHAT_MODEL: DEFAULT_CHAT_MODEL,
            CONF_API_MODE: DEFAULT_API_MODE,
            CONF_MAX_TOKENS: 500,
            CONF_WEB_SEARCH: False,
            CONF_ADVANCED_OPTIONS: False,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Basic Task"
    assert CONF_NAME not in result["data"]
    assert result["data"][CONF_MAX_TOKENS] == 500

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "ai_task_data"), context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Advanced Task",
            CONF_CHAT_MODEL: "gpt-4o",
            CONF_API_MODE: DEFAULT_API_MODE,
            CONF_MAX_TOKENS: 750,
            CONF_WEB_SEARCH: False,
            CONF_ADVANCED_OPTIONS: True,
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "advanced"
    schema_keys = {str(key) for key in result["data_schema"].schema}
    assert CONF_TOP_P in schema_keys
    assert CONF_TEMPERATURE in schema_keys
    assert CONF_SHORTEN_TOOL_CALL_ID in schema_keys

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_TOP_P: 0.8,
            CONF_TEMPERATURE: 0.3,
            CONF_SHORTEN_TOOL_CALL_ID: True,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Advanced Task"
    assert result["data"][CONF_CHAT_MODEL] == "gpt-4o"
    assert result["data"][CONF_MAX_TOKENS] == 750
    assert result["data"][CONF_TOP_P] == 0.8
    assert result["data"][CONF_TEMPERATURE] == 0.3
    assert result["data"][CONF_SHORTEN_TOOL_CALL_ID] is True


@pytest.mark.asyncio
async def test_ai_task_reconfigure_basic_and_advanced_update_real_subentry(
    hass: HomeAssistant,
) -> None:
    """Reconfigure an existing AI Task through HA on both save paths."""
    entry = _make_entry()
    await _setup_entry(hass, entry)
    ai_task_id = _subentry_id(entry, "ai_task_data")

    result = await entry.start_subentry_reconfigure_flow(hass, ai_task_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_CHAT_MODEL: "gpt-4o",
            CONF_API_MODE: DEFAULT_API_MODE,
            CONF_MAX_TOKENS: 900,
            CONF_WEB_SEARCH: False,
            CONF_ADVANCED_OPTIONS: False,
        },
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.subentries[ai_task_id].data[CONF_MAX_TOKENS] == 900

    result = await entry.start_subentry_reconfigure_flow(hass, ai_task_id)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_CHAT_MODEL: "gpt-4o",
            CONF_API_MODE: DEFAULT_API_MODE,
            CONF_MAX_TOKENS: 1000,
            CONF_WEB_SEARCH: True,
            CONF_ADVANCED_OPTIONS: True,
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "advanced"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_TOP_P: 0.7,
            CONF_TEMPERATURE: 0.2,
            CONF_SHORTEN_TOOL_CALL_ID: False,
        },
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    updated = entry.subentries[ai_task_id].data
    assert updated[CONF_MAX_TOKENS] == 1000
    assert updated[CONF_WEB_SEARCH] is True
    assert updated[CONF_TOP_P] == 0.7
    assert updated[CONF_TEMPERATURE] == 0.2


@pytest.mark.asyncio
async def test_options_flow_management_and_agent_test_round_trip(
    hass: HomeAssistant,
) -> None:
    """Drive the native options menu exactly as the HA frontend does."""
    entry = _make_entry()
    await _setup_entry(hass, entry)
    conversation_id = _subentry_id(entry, "conversation")

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "init"
    assert set(result["menu_options"]) == {
        "test_agent",
        "manage_memory",
        "manage_knowledge",
    }

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "manage_memory"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "manage_memory"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"panel_path": "/extended-openai-memory"}
    )
    assert result["type"] is FlowResultType.MENU

    with patch(
        f"{CONFIG_FLOW_MODULE}.async_test_agent",
        AsyncMock(return_value=SimpleNamespace(as_text=lambda: "agent test passed")),
    ) as test_agent:
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "test_agent"}
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "test_agent"
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"agent_id": conversation_id}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "test_result"
    report_key = next(key for key in result["data_schema"].schema if key == "report")
    assert report_key.default() == "agent test passed"
    test_agent.assert_awaited_once()

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"report": "agent test passed"}
    )
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "init"

    # Knowledge uses the same menu-return contract; cover its separate panel path.
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "manage_knowledge"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "manage_knowledge"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"panel_path": "/extended-openai-knowledge"}
    )
    assert result["type"] is FlowResultType.MENU
