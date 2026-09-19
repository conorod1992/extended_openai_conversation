"""Regression coverage for management behavior while Function Tools need repair."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from custom_components.extended_openai_conversation_responses import (
    management_function_quarantine as quarantine,
)
from custom_components.extended_openai_conversation_responses.agent_config import (
    agent_config_defaults,
)
from custom_components.extended_openai_conversation_responses.agent_test import (
    AgentTestResult,
    TestCheck,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_API_PROVIDER,
    CONF_CHAT_MODEL,
    CONF_FUNCTION_GROUPS,
    CONF_FUNCTION_TOOLS,
)


def _mixed_unknown_native_data() -> tuple[
    dict[str, Any], dict[str, Any], dict[str, Any]
]:
    defaults = agent_config_defaults()
    tools = yaml.safe_load(defaults[CONF_FUNCTION_TOOLS])
    assert isinstance(tools, list) and tools
    valid_tool = deepcopy(tools[0])
    invalid_tool = deepcopy(valid_tool)
    invalid_tool["spec"]["name"] = "schedule_deferred_action"
    invalid_tool["function"] = {
        "type": "native",
        "name": "deferred_actions.create_safe",
    }
    mixed = [valid_tool, invalid_tool]
    return (
        {
            **defaults,
            CONF_FUNCTION_TOOLS: yaml.safe_dump(
                mixed, sort_keys=False, allow_unicode=True
            ),
            CONF_FUNCTION_GROUPS: deepcopy(defaults[CONF_FUNCTION_GROUPS]),
        },
        valid_tool,
        invalid_tool,
    )


def test_safe_function_configuration_quarantines_unknown_native() -> None:
    """Management-only normalization keeps valid siblings and removes bad natives."""
    data, valid_tool, invalid_tool = _mixed_unknown_native_data()

    safe = quarantine._safe_function_configuration(data)
    parsed = yaml.safe_load(safe[CONF_FUNCTION_TOOLS])

    assert [tool["spec"]["name"] for tool in parsed] == [valid_tool["spec"]["name"]]
    assert invalid_tool["spec"]["name"] not in {
        name
        for group in safe[CONF_FUNCTION_GROUPS]
        for name in group.get("functions", [])
    }


def test_management_merge_preserves_repair_owned_function_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Saving Guest Mode cannot silently rewrite quarantined Function Tools."""
    data, _valid_tool, _invalid_tool = _mixed_unknown_native_data()
    original_tools = data[CONF_FUNCTION_TOOLS]
    original_groups = deepcopy(data[CONF_FUNCTION_GROUPS])

    monkeypatch.setattr(
        quarantine,
        "_STRICT_MERGE_AGENT_CONFIG",
        lambda source, updates: {**dict(source), **updates},
    )
    token = quarantine._ALLOW_QUARANTINED_TOOLS.set(True)
    try:
        merged = quarantine._management_merge_agent_config(
            data, {"guest_mode_enabled": True}
        )
    finally:
        quarantine._ALLOW_QUARANTINED_TOOLS.reset(token)

    assert merged["guest_mode_enabled"] is True
    assert merged[CONF_FUNCTION_TOOLS] == original_tools
    assert merged[CONF_FUNCTION_GROUPS] == original_groups


@pytest.mark.parametrize("section", ["request_rules", "guest_mode", "tools"])
def test_tolerant_management_scope_is_limited_and_reset_after_errors(
    monkeypatch, section
):
    sentinel = [{"spec": {"name": "valid"}, "function": {"type": "template"}}]
    monkeypatch.setattr(quarantine, "_usable_function_tools", lambda _: sentinel)

    def strict(_):
        raise AssertionError("strict parser used")

    monkeypatch.setattr(quarantine, "_STRICT_CONFIGURED_TOOLS", strict)
    with pytest.raises(RuntimeError, match="handler failed"):
        with quarantine.management_function_tools(section):
            assert (
                quarantine._management_configured_tools({CONF_FUNCTION_TOOLS: "broken"})
                == sentinel
            )
            raise RuntimeError("handler failed")
    with pytest.raises(AssertionError, match="strict parser used"):
        quarantine._management_configured_tools({})
    with quarantine.management_function_tools("configuration"):
        with pytest.raises(AssertionError, match="strict parser used"):
            quarantine._management_configured_tools({})


@pytest.mark.asyncio
async def test_diagnostics_quarantines_invalid_tools_and_reports_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bad Function Tool no longer prevents provider diagnostics from running."""
    data, valid_tool, invalid_tool = _mixed_unknown_native_data()
    entry = SimpleNamespace(entry_id="entry-1", runtime_data=object(), data={})
    subentry = SimpleNamespace(
        subentry_id="agent-1",
        title="Assistant",
        data=data,
    )
    observed: dict[str, Any] = {}

    async def original(_hass: Any, _entry: Any, safe_subentry: Any) -> AgentTestResult:
        observed["tools"] = yaml.safe_load(safe_subentry.data[CONF_FUNCTION_TOOLS])
        return AgentTestResult(
            "Passed",
            [TestCheck("Provider request", "Passed", "Request succeeded")],
        )

    monkeypatch.setattr(quarantine, "async_test_configured_agent", original)

    result = await quarantine._tolerant_agent_test(SimpleNamespace(), entry, subentry)

    assert [tool["spec"]["name"] for tool in observed["tools"]] == [
        valid_tool["spec"]["name"]
    ]
    assert invalid_tool["spec"]["name"] not in {
        tool["spec"]["name"] for tool in observed["tools"]
    }
    assert result.status == "Warning"
    assert result.checks[-1].name == "Function Tools"
    assert result.checks[-1].status == "Warning"
    assert "schedule_deferred_action" in result.checks[-1].message


@pytest.mark.asyncio
async def test_overview_fallback_uses_persisted_provider_and_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unrelated tool damage must not become Unknown provider / No model selected."""

    entry = SimpleNamespace(
        entry_id="entry-1",
        runtime_data=None,
        data={CONF_API_PROVIDER: "openai"},
    )
    subentry = SimpleNamespace(
        subentry_id="agent-1",
        data={CONF_CHAT_MODEL: "gpt-5.6-luna"},
    )

    from custom_components.extended_openai_conversation_responses import (
        management_setup_health,
    )

    def fail_health(*args, **kwargs):
        raise RuntimeError("registry unavailable")

    monkeypatch.setattr(
        management_setup_health, "build_setup_health_facts", fail_health
    )
    result = management_setup_health.add_setup_health(
        object(), entry, subentry, {"agent": {}}, is_admin=True
    )
    runtime = result["setup_health"]["provider_runtime"]
    assert runtime["provider"] == "openai"
    assert runtime["model"] == "gpt-5.6-luna"
    assert runtime["client_loaded"] is False
