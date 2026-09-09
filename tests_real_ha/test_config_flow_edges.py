"""Edge-path config-flow acceptance tests through Home Assistant's flow managers."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from homeassistant.config_entries import SOURCE_REAUTH, SOURCE_USER, ConfigEntryState
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
    CONF_BASE_URL,
    CONF_CHAT_MODEL,
    CONF_MAX_TOKENS,
    CONF_REASONING_EFFORT,
    CONF_SERVICE_TIER,
    CONF_SHORTEN_TOOL_CALL_ID,
    CONF_SKIP_AUTHENTICATION,
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


def _subentry(subentry_type: str, title: str, data: dict) -> dict:
    return {
        "data": data,
        "subentry_type": subentry_type,
        "title": title,
        "unique_id": None,
    }


def _make_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Config Flow Edge Acceptance",
        data={
            CONF_API_KEY: "sk-config-flow-edge-test",
            CONF_SKIP_AUTHENTICATION: True,
        },
        version=CONFIG_ENTRY_VERSION,
        subentries_data=[
            _subentry("conversation", "Conversation", agent_config_defaults()),
            _subentry("ai_task_data", "AI Task", dict(DEFAULT_AI_TASK_OPTIONS)),
        ],
    )


async def _setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED


async def _unload_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await hass.async_block_till_done()
    if entry.state is ConfigEntryState.LOADED:
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED


@pytest.mark.asyncio
async def test_user_flow_rejects_azure_without_custom_endpoint_before_authentication(
    hass: HomeAssistant,
) -> None:
    """Exercise Azure's required-endpoint validation through the real user flow."""
    with patch(
        f"{CONFIG_FLOW_MODULE}.get_authenticated_client",
        new_callable=AsyncMock,
    ) as authenticate:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_NAME: "Azure without endpoint",
                CONF_API_KEY: "sk-azure",
                CONF_BASE_URL: DEFAULT_CONF_BASE_URL,
                CONF_SKIP_AUTHENTICATION: True,
                CONF_API_PROVIDER: "azure",
            },
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "unknown"}
    authenticate.assert_not_awaited()


@pytest.mark.asyncio
async def test_reauth_missing_entry_aborts_through_flow_manager(
    hass: HomeAssistant,
) -> None:
    """The defensive reauth guard aborts cleanly if its entry vanished."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_REAUTH, "entry_id": "missing-entry"},
        data={},
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_failed"


@pytest.mark.asyncio
async def test_reauth_unexpected_validation_error_can_retry_in_same_flow(
    hass: HomeAssistant,
) -> None:
    """Unexpected reauth validation errors remain recoverable without restarting."""
    entry = _make_entry()
    entry.add_to_hass(hass)
    validate = AsyncMock(side_effect=[RuntimeError("validation exploded"), None])

    try:
        with patch(f"{CONFIG_FLOW_MODULE}.validate_input", validate):
            result = await entry.start_reauth_flow(hass)
            flow_id = result["flow_id"]
            result = await hass.config_entries.flow.async_configure(
                flow_id, {CONF_API_KEY: "replacement"}
            )
            assert result["type"] is FlowResultType.FORM
            assert result["errors"] == {"base": "unknown"}
            assert entry.data[CONF_API_KEY] == "sk-config-flow-edge-test"

            result = await hass.config_entries.flow.async_configure(
                flow_id, {CONF_API_KEY: "replacement"}
            )

        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "reauth_successful"
        assert entry.data[CONF_API_KEY] == "replacement"
        assert validate.await_count == 2
    finally:
        await _unload_entry(hass, entry)


@pytest.mark.asyncio
async def test_ai_task_subentry_flow_aborts_when_parent_not_loaded(
    hass: HomeAssistant,
) -> None:
    """AI Task lifecycle forms reject an inactive parent entry."""
    entry = _make_entry()
    entry.add_to_hass(hass)

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "ai_task_data"), context={"source": SOURCE_USER}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "entry_not_loaded"


@pytest.mark.asyncio
async def test_ai_task_basic_creation_uses_default_title_when_name_omitted(
    hass: HomeAssistant,
) -> None:
    """The real subentry flow handles API submissions that omit the optional name."""
    entry = _make_entry()
    await _setup_entry(hass, entry)
    try:
        result = await hass.config_entries.subentries.async_init(
            (entry.entry_id, "ai_task_data"), context={"source": SOURCE_USER}
        )
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"],
            {
                CONF_CHAT_MODEL: DEFAULT_CHAT_MODEL,
                CONF_API_MODE: DEFAULT_API_MODE,
                CONF_MAX_TOKENS: 500,
                CONF_WEB_SEARCH: False,
                CONF_ADVANCED_OPTIONS: False,
            },
        )

        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["title"] == DEFAULT_AI_TASK_NAME
        assert CONF_NAME not in result["data"]
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED
    finally:
        await _unload_entry(hass, entry)


@pytest.mark.asyncio
async def test_ai_task_advanced_reasoning_model_uses_capability_specific_schema(
    hass: HomeAssistant,
) -> None:
    """Reasoning models hide sampling controls and expose reasoning/service controls."""
    entry = _make_entry()
    await _setup_entry(hass, entry)
    try:
        result = await hass.config_entries.subentries.async_init(
            (entry.entry_id, "ai_task_data"), context={"source": SOURCE_USER}
        )
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"],
            {
                CONF_NAME: "Reasoning Task",
                CONF_CHAT_MODEL: "gpt-5-mini",
                CONF_API_MODE: DEFAULT_API_MODE,
                CONF_MAX_TOKENS: 750,
                CONF_WEB_SEARCH: False,
                CONF_ADVANCED_OPTIONS: True,
            },
        )

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "advanced"
        schema_keys = {str(key) for key in result["data_schema"].schema}
        assert CONF_TOP_P not in schema_keys
        assert CONF_TEMPERATURE not in schema_keys
        assert CONF_REASONING_EFFORT in schema_keys
        assert CONF_SERVICE_TIER in schema_keys
        assert CONF_SHORTEN_TOOL_CALL_ID in schema_keys

        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"],
            {
                CONF_REASONING_EFFORT: "medium",
                CONF_SERVICE_TIER: "auto",
                CONF_SHORTEN_TOOL_CALL_ID: False,
            },
        )

        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["title"] == "Reasoning Task"
        assert result["data"][CONF_REASONING_EFFORT] == "medium"
        assert result["data"][CONF_SERVICE_TIER] == "auto"
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED
    finally:
        await _unload_entry(hass, entry)
