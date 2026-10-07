"""Cross-layer contracts for user-facing runtime options and diagnostics."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from openai import OpenAIError
import pytest

from custom_components.extended_openai_conversation_responses import agent_test
from custom_components.extended_openai_conversation_responses.agent_test import async_test_agent
from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
    CONF_API_MODE,
    CONF_ARCHIVE_ENABLED,
    CONF_ARCHIVE_MODEL_SEARCH_ENABLED,
    CONF_CHAT_MODEL,
    CONF_FUNCTION_TOOLS,
    CONF_MEMORY_MODE,
    MEMORY_MODE_OFF,
)
from custom_components.extended_openai_conversation_responses.guest_mode import (
    guest_mode_restrict_tool,
)
from custom_components.extended_openai_conversation_responses.request import (
    assemble_integration_function_tools,
)


class _HttpProviderError(OpenAIError):
    """Small SDK-shaped error carrying one HTTP status."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"provider failure {status_code}")
        self.status_code = status_code


def _diagnostic_objects(status_code: int):
    subentry = SimpleNamespace(
        subentry_id="agent-1",
        subentry_type="conversation",
        title="Assistant",
        data={
            CONF_CHAT_MODEL: "gpt-4.1-mini",
            CONF_API_MODE: API_MODE_CHAT_COMPLETIONS,
            CONF_FUNCTION_TOOLS: "[]",
            CONF_MEMORY_MODE: MEMORY_MODE_OFF,
        },
    )
    create = AsyncMock(side_effect=_HttpProviderError(status_code))
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    entry = SimpleNamespace(
        entry_id="entry-1",
        data={},
        runtime_data=client,
        async_start_reauth=MagicMock(),
    )
    hass = MagicMock()
    usage = SimpleNamespace(async_record_request=AsyncMock())
    return hass, entry, subentry, usage


@pytest.mark.parametrize(
    ("status_code", "expected_fragment"),
    [
        (403, "HTTP 403"),
        (429, "HTTP 429"),
        (500, "HTTP 500"),
        (503, "HTTP 503"),
    ],
)
async def test_diagnostics_provider_failures_do_not_masquerade_as_authentication(
    status_code: int,
    expected_fragment: str,
) -> None:
    """Only an authentication rejection may turn the Authentication check red."""
    hass, entry, subentry, usage = _diagnostic_objects(status_code)
    with (
        patch(
            "custom_components.extended_openai_conversation_responses.agent_test.get_exposed_entities",
            return_value=[SimpleNamespace()],
        ),
        patch(
            "custom_components.extended_openai_conversation_responses.agent_test.async_get_usage",
            AsyncMock(return_value=usage),
        ),
    ):
        result = await async_test_agent(hass, entry, subentry)

    checks = {check.name: check for check in result.checks}
    assert result.status == "Failed"
    assert result.authentication_rejected is False
    assert checks["Authentication"].status == "Passed"
    assert checks["Model access"].status == "Failed"
    assert expected_fragment in checks["Model access"].message
    entry.async_start_reauth.assert_not_called()


async def test_diagnostics_http_401_is_the_authentication_failure_boundary() -> None:
    """The same live probe path reserves reauthentication for HTTP 401."""
    hass, entry, subentry, usage = _diagnostic_objects(401)
    with (
        patch(
            "custom_components.extended_openai_conversation_responses.agent_test.get_exposed_entities",
            return_value=[SimpleNamespace()],
        ),
        patch(
            "custom_components.extended_openai_conversation_responses.agent_test.async_get_usage",
            AsyncMock(return_value=usage),
        ),
    ):
        result = await async_test_agent(hass, entry, subentry)

    checks = {check.name: check for check in result.checks}
    assert result.authentication_rejected is True
    assert checks["Authentication"].status == "Failed"
    assert checks["Model access"].message == "Authentication rejected"
    entry.async_start_reauth.assert_called_once_with(hass)


@pytest.mark.parametrize(
    ("archive_enabled", "model_search_enabled", "expected_search"),
    [
        (False, False, False),
        (False, True, False),
        (True, False, False),
        (True, True, True),
    ],
)
def test_archive_model_search_option_controls_model_read_tools(
    archive_enabled: bool,
    model_search_enabled: bool,
    expected_search: bool,
) -> None:
    """Archive search/get are exposed only when saving and model search are enabled."""
    tools = assemble_integration_function_tools(
        {
            CONF_ARCHIVE_ENABLED: archive_enabled,
            CONF_ARCHIVE_MODEL_SEARCH_ENABLED: model_search_enabled,
        },
        set(),
        memory_scope_available=False,
        temporary_scope_available=False,
        knowledge_available=False,
        archive_available=True,
    )
    names = {tool["spec"]["name"] for tool in tools}
    assert ("conversation_search" in names) is expected_search
    assert ("conversation_get" in names) is expected_search
    if not archive_enabled:
        assert not any(name.startswith("conversation_") for name in names)


def test_llm_guest_mode_tool_is_structurally_one_way() -> None:
    """The model-facing schema cannot express trusted disable/replace operations."""
    tool = guest_mode_restrict_tool()
    assert tool["function"] == {"type": "guest_mode", "operation": "restrict"}
    parameters = tool["spec"]["parameters"]
    assert parameters["additionalProperties"] is False
    assert set(parameters["properties"]) == {
        "active_from",
        "active_until",
        "make_indefinite",
    }
    forbidden = {"enabled", "disable", "cancel", "operation", "indefinite"}
    assert forbidden.isdisjoint(parameters["properties"])
    assert "cannot disable, cancel, delay, or shorten" in tool["spec"]["description"]
