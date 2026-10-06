"""Last nested residuals that still represent reachable behavior."""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from homeassistant.helpers.typing import UNDEFINED


@pytest.mark.asyncio
async def test_repair_configuration_save_preserves_absent_function_fields(
    hass, monkeypatch
) -> None:
    from custom_components.extended_openai_conversation_responses import (
        management_function_repair as repair,
        management_loading_performance as loading,
        management_ui,
    )

    subentry = SimpleNamespace(
        subentry_id="agent",
        data={},
        title="Agent",
    )
    entry = SimpleNamespace(
        entry_id="entry",
        title="Entry",
        data={},
        subentries={"agent": subentry},
    )
    monkeypatch.setattr(management_ui, "_require_admin", Mock())
    monkeypatch.setattr(
        management_ui,
        "entry_and_agent",
        Mock(return_value=(entry, subentry)),
    )
    monkeypatch.setattr(repair, "function_tools_issue", Mock(return_value=([], "broken")))
    monkeypatch.setattr(
        repair,
        "_safe_configuration_payload",
        Mock(return_value={"config": {}}),
    )
    monkeypatch.setattr(repair, "_function_fields_unchanged", Mock(return_value=True))
    monkeypatch.setattr(repair, "safe_function_configuration", Mock(return_value={}))
    monkeypatch.setattr(
        management_ui,
        "_validation_result",
        Mock(return_value={"valid": True, "config": {}}),
    )
    monkeypatch.setattr(repair, "preserve_legacy_guest_policy", lambda raw, value: value)
    update = Mock()
    monkeypatch.setattr(repair, "update_live_subentry", update)
    monkeypatch.setattr(repair, "saved_agent_config_revision", Mock(return_value="rev"))
    monkeypatch.setattr(
        loading,
        "_snapshot_normalized_configuration",
        lambda value, validated=False: dict(value),
    )

    await repair.async_function_repair(
        hass,
        "user",
        True,
        {
            "entry_id": "entry",
            "subentry_id": "agent",
            "action": "configuration_save",
            "config": {},
        },
    )

    persisted = update.call_args.kwargs["data"]
    assert repair.CONF_FUNCTION_TOOLS not in persisted
    assert repair.CONF_FUNCTION_GROUPS not in persisted


@pytest.mark.asyncio
async def test_request_rule_uses_async_stop_on_older_script_api(hass, monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses import request_rules

    monkeypatch.setattr(
        request_rules,
        "_outcome_probes",
        lambda actions: (list(actions), "completed", "stopped"),
    )
    monkeypatch.setattr(request_rules.cv, "SCRIPT_SCHEMA", lambda actions: actions)
    monkeypatch.setattr(
        request_rules,
        "async_validate_actions_config",
        AsyncMock(side_effect=lambda hass, actions: actions),
    )
    stopped = AsyncMock()

    class FakeScript:
        def __init__(self, *args, **kwargs):
            pass

        async def async_run(self, variables, context):
            return SimpleNamespace(
                variables={"completed": True},
                conversation_response=UNDEFINED,
            )

        async_stop = stopped

    monkeypatch.setattr(request_rules, "Script", FakeScript)
    rule = {
        "id": "r1",
        "name": "Rule",
        "action_type": "local_action",
        "action": {
            "actions": [{"delay": 0}],
            "continue_to_ai": False,
            "failure_response": "failed",
            "success_response": "ok",
        },
    }

    result = await request_rules._async_evaluate_matched_rule(
        hass,
        request_rules.RuleMatch(rule, "phrase", False, 100.0),
        "hello",
        SimpleNamespace(),
        "session",
        "model",
        None,
        30,
        None,
        None,
    )

    assert result.successful is True
    stopped.assert_awaited_once()


@pytest.mark.asyncio
async def test_request_rule_failure_rechecks_matching_revision(
    hass, monkeypatch
) -> None:
    from custom_components.extended_openai_conversation_responses import request_rules

    monkeypatch.setattr(
        request_rules,
        "_outcome_probes",
        lambda actions: (list(actions), "completed", "stopped"),
    )
    monkeypatch.setattr(request_rules.cv, "SCRIPT_SCHEMA", lambda actions: actions)
    monkeypatch.setattr(
        request_rules,
        "async_validate_actions_config",
        AsyncMock(side_effect=lambda hass, actions: actions),
    )

    class FakeScript:
        def __init__(self, *args, **kwargs):
            pass

        async def async_run(self, variables, context):
            raise RuntimeError("script failed")

        async def async_unload(self):
            return None

    monkeypatch.setattr(request_rules, "Script", FakeScript)
    revision = Mock()
    rule = {
        "id": "r1",
        "name": "Rule",
        "action_type": "local_action",
        "action": {
            "actions": [{"delay": 0}],
            "continue_to_ai": False,
            "failure_response": "failed",
            "success_response": "ok",
        },
    }

    result = await request_rules._async_evaluate_matched_rule(
        hass,
        request_rules.RuleMatch(rule, "phrase", False, 100.0),
        "hello",
        SimpleNamespace(),
        "session",
        "model",
        None,
        30,
        None,
        None,
        require_matching_revision=revision,
    )

    assert result.successful is False
    assert result.response == "failed"
    assert revision.call_count >= 3


@pytest.mark.asyncio
async def test_agent_test_generic_probe_failure_reports_web_search(
    hass, monkeypatch
) -> None:
    from custom_components.extended_openai_conversation_responses import agent_test
    from custom_components.extended_openai_conversation_responses import guest_mode

    @contextmanager
    def snapshot(_model, _metadata):
        yield

    usage = SimpleNamespace(async_record_request=AsyncMock())
    client = SimpleNamespace(
        responses=SimpleNamespace(create=AsyncMock(side_effect=RuntimeError("boom")))
    )
    entry = SimpleNamespace(
        entry_id="entry",
        data={},
        runtime_data=client,
    )
    subentry = SimpleNamespace(
        subentry_id="agent",
        subentry_type="conversation",
        data={
            agent_test.CONF_WEB_SEARCH: True,
            agent_test.CONF_API_MODE: agent_test.API_MODE_RESPONSES,
        },
    )
    monkeypatch.setattr(agent_test, "model_metadata", Mock(return_value={}))
    monkeypatch.setattr(agent_test, "model_capability_snapshot", snapshot)
    monkeypatch.setattr(agent_test, "get_api_mode", Mock(return_value=agent_test.API_MODE_RESPONSES))
    monkeypatch.setattr(agent_test, "capability_allowed", Mock(return_value=True))
    monkeypatch.setattr(agent_test, "supports_openai_hosted_tools", Mock(return_value=True))
    monkeypatch.setattr(agent_test, "_validate_function_schema", Mock(return_value=0))
    monkeypatch.setattr(agent_test, "get_loaded_guest_mode", Mock(return_value=None))
    monkeypatch.setattr(agent_test, "configured_function_tools_from_data", Mock(return_value=[]))
    monkeypatch.setattr(
        agent_test,
        "resolve_guest_policy",
        Mock(return_value=guest_mode.GuestCapabilityPolicy.unrestricted()),
    )
    monkeypatch.setattr(agent_test, "get_exposed_entities", Mock(return_value=[]))
    monkeypatch.setattr(agent_test, "memory_enabled", Mock(return_value=False))
    monkeypatch.setattr(agent_test, "conversation_tools_required", Mock(return_value=False))
    monkeypatch.setattr(agent_test, "async_get_usage", AsyncMock(return_value=usage))

    result = await agent_test.async_test_agent(hass, entry, subentry)

    web = next(check for check in result.checks if check.name == "Web Search")
    assert web.status == "Failed"
    assert "boom" in web.message
    usage.async_record_request.assert_awaited()
