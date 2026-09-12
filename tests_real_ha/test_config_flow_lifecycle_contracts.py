"""Config-flow persistence and runtime-consumption contracts through real HA flows."""

from __future__ import annotations

from copy import deepcopy
from unittest.mock import AsyncMock, patch

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
    CONF_API_PROVIDER,
    CONF_API_VERSION,
    CONF_BASE_URL,
    CONF_ORGANIZATION,
    CONF_SKIP_AUTHENTICATION,
    CONFIG_ENTRY_VERSION,
    DEFAULT_AI_TASK_OPTIONS,
    DEFAULT_API_PROVIDER,
    DEFAULT_CONVERSATION_NAME,
    DOMAIN,
)

CONFIG_FLOW_MODULE = (
    "custom_components.extended_openai_conversation_responses.config_flow"
)
INTEGRATION_MODULE = "custom_components.extended_openai_conversation_responses"


def _subentry(subentry_type: str, title: str, data: dict) -> dict:
    return {
        "data": data,
        "subentry_type": subentry_type,
        "title": title,
        "unique_id": None,
    }


def _entry(*, data: dict, options: dict | None = None) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Lifecycle Contract",
        data=data,
        options=options or {},
        version=CONFIG_ENTRY_VERSION,
        subentries_data=[
            _subentry("conversation", "Conversation", agent_config_defaults()),
            _subentry("ai_task_data", "AI Task", dict(DEFAULT_AI_TASK_OPTIONS)),
        ],
    )


async def _start_user_flow(hass: HomeAssistant) -> dict:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    return result


@pytest.mark.asyncio
async def test_user_flow_persists_exact_provider_data_and_default_subentries(
    hass: HomeAssistant,
) -> None:
    """Explicit falsy/empty provider values survive the public user flow unchanged."""
    submitted = {
        CONF_NAME: "Lifecycle Provider",
        CONF_API_KEY: "sk-lifecycle",
        CONF_BASE_URL: "https://example.test/v1",
        CONF_API_VERSION: "2026-09-01",
        CONF_ORGANIZATION: "",
        CONF_SKIP_AUTHENTICATION: False,
        CONF_API_PROVIDER: "openai",
    }

    with patch(
        f"{CONFIG_FLOW_MODULE}.get_authenticated_client", new_callable=AsyncMock
    ) as authenticate:
        result = await _start_user_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], dict(submitted)
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Lifecycle Provider"
    assert dict(result["data"]) == submitted

    entry = result["result"]
    assert dict(entry.data) == submitted
    assert entry.options == {}
    by_type = {item.subentry_type: item for item in entry.subentries.values()}
    assert dict(by_type["conversation"].data) == agent_config_defaults()
    assert dict(by_type["ai_task_data"].data) == dict(DEFAULT_AI_TASK_OPTIONS)

    authenticate.assert_awaited_once()
    assert authenticate.await_args.kwargs["organization"] == ""
    assert authenticate.await_args.kwargs["skip_authentication"] is False


@pytest.mark.asyncio
async def test_user_flow_omitted_optional_values_use_schema_defaults(
    hass: HomeAssistant,
) -> None:
    """Omitted optionals use current form defaults instead of falsey fallbacks."""
    with patch(
        f"{CONFIG_FLOW_MODULE}.get_authenticated_client", new_callable=AsyncMock
    ) as authenticate:
        result = await _start_user_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_API_KEY: "sk-defaults"}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "ChatGPT"
    assert result["data"][CONF_NAME] == "ChatGPT"
    assert result["data"][CONF_API_PROVIDER] == DEFAULT_API_PROVIDER
    assert result["data"][CONF_SKIP_AUTHENTICATION] is False
    # validate_input intentionally avoids persisting the canonical OpenAI endpoint.
    assert CONF_BASE_URL not in result["data"]

    authenticate.assert_awaited_once()
    assert authenticate.await_args.kwargs["base_url"] is None
    assert authenticate.await_args.kwargs["skip_authentication"] is False


@pytest.mark.asyncio
async def test_user_flow_validation_error_does_not_create_entry(
    hass: HomeAssistant,
) -> None:
    """A failed provider validation leaves config-entry storage untouched."""
    before = tuple(entry.entry_id for entry in hass.config_entries.async_entries(DOMAIN))

    with patch(
        f"{CONFIG_FLOW_MODULE}.validate_input",
        AsyncMock(side_effect=RuntimeError("validation failed")),
    ):
        result = await _start_user_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_NAME: "Must Not Persist",
                CONF_API_KEY: "sk-invalid",
                CONF_SKIP_AUTHENTICATION: True,
                CONF_API_PROVIDER: "openai",
            },
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "unknown"}
    after = tuple(entry.entry_id for entry in hass.config_entries.async_entries(DOMAIN))
    assert after == before


@pytest.mark.asyncio
async def test_reauth_validation_failure_is_fully_atomic(
    hass: HomeAssistant,
) -> None:
    """Failed reauthentication cannot partially mutate entry or subentry state."""
    entry = _entry(
        data={
            CONF_API_KEY: "expired",
            CONF_API_PROVIDER: "azure",
            CONF_BASE_URL: "https://example.openai.azure.com",
            CONF_API_VERSION: "2026-09-01",
            CONF_ORGANIZATION: "org-before",
            CONF_SKIP_AUTHENTICATION: False,
        },
        options={"preserved_option": "value"},
    )
    entry.add_to_hass(hass)
    before_data = dict(entry.data)
    before_options = dict(entry.options)
    before_subentries = {
        subentry_id: deepcopy(dict(subentry.data))
        for subentry_id, subentry in entry.subentries.items()
    }

    with patch(
        f"{CONFIG_FLOW_MODULE}.validate_input",
        AsyncMock(side_effect=RuntimeError("reauth validation failed")),
    ):
        result = await entry.start_reauth_flow(hass)
        assert result["type"] is FlowResultType.FORM
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_API_KEY: "replacement"}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    assert result["errors"] == {"base": "unknown"}
    assert dict(entry.data) == before_data
    assert dict(entry.options) == before_options
    assert {
        subentry_id: dict(subentry.data)
        for subentry_id, subentry in entry.subentries.items()
    } == before_subentries


@pytest.mark.asyncio
async def test_user_flow_output_is_consumed_by_runtime_setup(
    hass: HomeAssistant,
) -> None:
    """Runtime authentication consumes the exact provider values emitted by the flow."""
    submitted = {
        CONF_NAME: "Runtime Contract",
        CONF_API_KEY: "sk-runtime-contract",
        CONF_BASE_URL: "https://runtime.example.test/v1",
        CONF_API_VERSION: "2026-09-01",
        CONF_ORGANIZATION: "runtime-org",
        CONF_SKIP_AUTHENTICATION: True,
        CONF_API_PROVIDER: "openai",
    }

    with patch(
        f"{CONFIG_FLOW_MODULE}.get_authenticated_client", new_callable=AsyncMock
    ):
        result = await _start_user_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], dict(submitted)
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    entry = result["result"]
    assert dict(entry.data) == submitted

    runtime_client = object()
    authenticate = AsyncMock(return_value=runtime_client)
    forward = AsyncMock(return_value=None)
    setup_templates = AsyncMock(return_value=None)
    unload_platforms = AsyncMock(return_value=True)
    unload_templates = AsyncMock(return_value=None)

    with (
        patch(f"{INTEGRATION_MODULE}.get_authenticated_client", authenticate),
        patch(f"{INTEGRATION_MODULE}.DebugOpenAIClientProxy", side_effect=lambda value: value),
        patch(
            f"{INTEGRATION_MODULE}.PerformanceOpenAIClientProxy",
            side_effect=lambda value, **_kwargs: value,
        ),
        patch.object(hass.config_entries, "async_forward_entry_setups", forward),
        patch(f"{INTEGRATION_MODULE}.async_setup_templates", setup_templates),
        patch.object(hass.config_entries, "async_unload_platforms", unload_platforms),
        patch(f"{INTEGRATION_MODULE}.async_unload_templates", unload_templates),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED

        authenticate.assert_awaited_once_with(
            hass=hass,
            api_key=submitted[CONF_API_KEY],
            base_url=submitted[CONF_BASE_URL],
            api_version=submitted[CONF_API_VERSION],
            organization=submitted[CONF_ORGANIZATION],
            skip_authentication=submitted[CONF_SKIP_AUTHENTICATION],
            api_provider=submitted[CONF_API_PROVIDER],
        )
        assert entry.runtime_data is runtime_client

        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.NOT_LOADED
