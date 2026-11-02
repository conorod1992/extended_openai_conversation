"""Diagnostics retain independent facts across persisted representations/failures."""

from copy import deepcopy
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.extended_openai_conversation_responses import (
    diagnostics as module,
)
from custom_components.extended_openai_conversation_responses.agent_config import (
    AgentConfigError,
    parse_configured_function_tools,
    validate_function_tools,
)
from tests.function_configuration_corpus import (
    FUNCTION_CONFIGURATION_CASES,
    corpus_config,
)
from tests.test_diagnostics import (
    _conversation_subentry,
    _entry,
    _patch_healthy_collectors,
)


@pytest.mark.parametrize(
    "case", FUNCTION_CONFIGURATION_CASES, ids=lambda case: case["id"]
)
def test_diagnostic_function_reader_matches_canonical_persisted_reader(case):
    data = corpus_config(case)
    before = deepcopy(data)
    if case["effective"] == "quarantined":
        with pytest.raises(AgentConfigError):
            module._configured_function_tools(data)
    else:
        assert module._configured_function_tools(data) == validate_function_tools(
            parse_configured_function_tools(data)
        )
    assert data == before


async def test_structured_functions_and_invalid_groups_keep_counts_and_guest_status(
    hass, monkeypatch
):
    read_tools = module._configured_function_tools
    validate_groups = module.validate_function_groups
    collectors = _patch_healthy_collectors(monkeypatch)
    monkeypatch.setattr(module, "_configured_function_tools", read_tools)
    monkeypatch.setattr(module, "validate_function_groups", validate_groups)
    tools = parse_configured_function_tools({})
    data = {"functions": tools, "function_groups": 42}
    result = await module.async_get_config_entry_diagnostics(
        hass, _entry(_conversation_subentry(data=data))
    )
    agent = result["conversation_agents"][0]
    assert agent["configured_function_tools"] == len(tools)
    assert agent["function_group_configuration_error"] == "AgentConfigError"
    assert agent["guest_mode"] == {"status": {}, "policy": {}}
    assert agent["memory_items"] == 2 and agent["archive"] == {"archive_items": 4}
    collectors.guest.assert_awaited_once()


@pytest.mark.parametrize(
    "failed", ["continuity", "group_runtime", "guest_store", "guest_policy"]
)
async def test_diagnostic_section_failures_preserve_other_sections(
    hass, monkeypatch, failed
):
    _patch_healthy_collectors(monkeypatch)
    failure = OSError("private source or endpoint must not escape diagnostics")
    key = {
        "continuity": "async_get_continuity",
        "group_runtime": "get_function_group_runtime",
        "guest_store": "async_get_guest_mode",
        "guest_policy": "resolve_guest_policy",
    }[failed]
    monkeypatch.setattr(
        module,
        key,
        AsyncMock(side_effect=failure)
        if failed == "guest_store"
        else MagicMock(side_effect=failure),
    )
    result = await module.async_get_config_entry_diagnostics(
        hass, _entry(_conversation_subentry())
    )
    agent = result["conversation_agents"][0]
    assert agent["configured_function_tools"] == 0
    assert agent["configured_function_groups"] == 0
    assert agent["temporary_memory_items"] == 1 and agent["knowledge_items"] == 3
    assert agent["archive"] == {"archive_items": 4}
    if failed == "guest_store":
        assert agent["guest_mode_storage_error"] == "OSError"
    elif failed == "guest_policy":
        assert agent["guest_mode"] == {"status": {}, "policy_error": "OSError"}
    else:
        assert agent["guest_mode"] == {"status": {}, "policy": {}}
        assert (
            agent[
                "continuity_error"
                if failed == "continuity"
                else "function_group_runtime_error"
            ]
            == "OSError"
        )
    assert str(failure) not in str(result)
