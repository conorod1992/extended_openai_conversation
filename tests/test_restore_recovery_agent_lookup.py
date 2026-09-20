"""Regression coverage for exact live-agent lookup during restore cleanup."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses import restore_recovery
from homeassistant.components import conversation
from homeassistant.components.conversation.const import DATA_COMPONENT


def test_active_agent_finds_exact_subentry_when_parent_mapping_points_elsewhere(
    monkeypatch,
) -> None:
    """Multiple conversation subentries cannot hide the restored live entity."""
    parent = SimpleNamespace(entry_id="entry-1")
    other = SimpleNamespace(
        entry=parent,
        subentry=SimpleNamespace(subentry_id="agent-other"),
    )
    target = SimpleNamespace(
        entry=parent,
        subentry=SimpleNamespace(subentry_id="agent-target"),
    )
    hass = SimpleNamespace(
        data={DATA_COMPONENT: SimpleNamespace(entities=(other, target))}
    )
    monkeypatch.setattr(conversation, "async_get_agent", lambda *_args: other)

    assert restore_recovery._active_agent(hass, "entry-1", "agent-target") is target


def test_active_agent_prefers_exact_registered_agent(monkeypatch) -> None:
    from homeassistant.components import conversation

    exact = SimpleNamespace(
        entry=SimpleNamespace(entry_id="entry-1"),
        subentry=SimpleNamespace(subentry_id="agent-new"),
    )
    hass = SimpleNamespace(data={})
    monkeypatch.setattr(conversation, "async_get_agent", lambda *_args: exact)

    assert restore_recovery._active_agent(hass, "entry-1", "agent-new") is exact


@pytest.mark.parametrize("exc", [KeyError("missing"), ValueError("bad")])
def test_active_agent_scans_loaded_entities_when_core_lookup_fails(
    monkeypatch, exc
) -> None:
    from homeassistant.components import conversation

    wrong = SimpleNamespace(
        entry=SimpleNamespace(entry_id="entry-1"),
        subentry=SimpleNamespace(subentry_id="other-agent"),
    )
    exact = SimpleNamespace(
        entry=SimpleNamespace(entry_id="entry-1"),
        subentry=SimpleNamespace(subentry_id="agent-new"),
    )
    component = SimpleNamespace(entities=[wrong, exact])
    hass = SimpleNamespace(data={conversation.DATA_COMPONENT: component})

    def fail_lookup(*_args):
        raise exc

    monkeypatch.setattr(conversation, "async_get_agent", fail_lookup)

    assert restore_recovery._active_agent(hass, "entry-1", "agent-new") is exact


def test_active_agent_returns_none_when_no_exact_agent_exists(monkeypatch) -> None:
    from homeassistant.components import conversation

    wrong = SimpleNamespace(
        entry=SimpleNamespace(entry_id="entry-1"),
        subentry=SimpleNamespace(subentry_id="other-agent"),
    )
    hass = SimpleNamespace(
        data={conversation.DATA_COMPONENT: SimpleNamespace(entities=[wrong])}
    )
    monkeypatch.setattr(conversation, "async_get_agent", lambda *_args: wrong)

    assert restore_recovery._active_agent(hass, "entry-1", "agent-new") is None


def test_runtime_reset_clears_distinct_registry_and_agent_runtime_objects(
    monkeypatch,
) -> None:
    from custom_components.extended_openai_conversation_responses import (
        continuity,
        function_groups,
        request_rules,
    )

    def continuity_state(label):
        return SimpleNamespace(
            label=label,
            _sessions={"x": 1},
            _memory_bundles={"x": 1},
            _pending_ends={"x"},
            _ignored_conversation_ids={"x": 1},
        )

    def rule_state(label):
        return SimpleNamespace(label=label, _conversation_overrides={"x": 1})

    def group_state(label):
        return SimpleNamespace(label=label, _sessions={"x": 1}, _last_request={"x": 1})

    key = ("entry-1", "agent-new")
    registry_continuity = continuity_state("registry")
    agent_continuity = continuity_state("agent")
    registry_rules = rule_state("registry")
    agent_rules = rule_state("agent")
    registry_groups = group_state("registry")
    agent_groups = group_state("agent")
    agent = SimpleNamespace(
        _continuity=agent_continuity,
        _request_rule_runtime=agent_rules,
        _function_groups_runtime=agent_groups,
        _usage=None,
    )
    hass = SimpleNamespace(
        data={
            continuity._MANAGERS: {key: registry_continuity},
            request_rules._RUNTIMES: {key: registry_rules},
            function_groups._RUNTIMES: {key: registry_groups},
        }
    )
    monkeypatch.setattr(restore_recovery, "_active_agent", lambda *_args: agent)

    restore_recovery.reset_restored_runtime(hass, *key)

    for current in (registry_continuity, agent_continuity):
        assert current._sessions == {}
        assert current._memory_bundles == {}
        assert current._pending_ends == set()
        assert current._ignored_conversation_ids == {}
    for current in (registry_rules, agent_rules):
        assert current._conversation_overrides == {}
    for current in (registry_groups, agent_groups):
        assert current._sessions == {}
        assert current._last_request == {}
    assert hass.data[continuity._MANAGERS][key] is agent_continuity
    assert hass.data[request_rules._RUNTIMES][key] is agent_rules
    assert hass.data[function_groups._RUNTIMES][key] is agent_groups


def _continuity_runtime(marker: str):
    return SimpleNamespace(
        _sessions={marker: object()},
        _memory_bundles={marker: object()},
        _pending_ends={marker},
        _ignored_conversation_ids={marker: None},
    )


def _rule_runtime(marker: str):
    return SimpleNamespace(_conversation_overrides={marker: object()})


def _group_runtime(marker: str):
    return SimpleNamespace(
        _sessions={marker: object()},
        _last_request={marker: object()},
    )


def test_runtime_reset_reconciles_distinct_registry_and_agent_objects(
    monkeypatch,
) -> None:
    """Restore clears both stale copies then makes the live agent copy authoritative."""
    from custom_components.extended_openai_conversation_responses import (
        continuity,
        function_groups,
        request_rules,
        usage,
    )

    key = ("entry-1", "agent-1")
    registry_continuity = _continuity_runtime("registry")
    agent_continuity = _continuity_runtime("agent")
    registry_rules = _rule_runtime("registry")
    agent_rules = _rule_runtime("agent")
    registry_groups = _group_runtime("registry")
    agent_groups = _group_runtime("agent")
    fallback_usage = object()
    unrelated_usage = object()
    durable_usage = object()
    agent = SimpleNamespace(
        _continuity=agent_continuity,
        _request_rule_runtime=agent_rules,
        _function_groups_runtime=agent_groups,
        _usage=unrelated_usage,
    )
    hass = SimpleNamespace(
        data={
            continuity._MANAGERS: {key: registry_continuity},
            request_rules._RUNTIMES: {key: registry_rules},
            function_groups._RUNTIMES: {key: registry_groups},
            usage._VOLATILE_USAGE_MANAGERS: {key: fallback_usage},
            restore_recovery.SUBSYSTEM_STATUS_KEY: {key: {"status": "degraded"}},
        }
    )
    managers = (
        object(),
        object(),
        object(),
        object(),
        durable_usage,
        object(),
        object(),
    )
    monkeypatch.setattr(restore_recovery, "_active_agent", lambda *_args: agent)

    restore_recovery.reset_restored_runtime(hass, *key, managers)

    for runtime in (registry_continuity, agent_continuity):
        assert runtime._sessions == {}
        assert runtime._memory_bundles == {}
        assert runtime._pending_ends == set()
        assert runtime._ignored_conversation_ids == {}
    for runtime in (registry_rules, agent_rules):
        assert runtime._conversation_overrides == {}
    for runtime in (registry_groups, agent_groups):
        assert runtime._sessions == {}
        assert runtime._last_request == {}

    assert hass.data[continuity._MANAGERS][key] is agent_continuity
    assert hass.data[request_rules._RUNTIMES][key] is agent_rules
    assert hass.data[function_groups._RUNTIMES][key] is agent_groups
    assert key not in hass.data[usage._VOLATILE_USAGE_MANAGERS]
    # Only the exact volatile fallback pointer may be replaced.
    assert agent._usage is unrelated_usage
    assert key not in hass.data[restore_recovery.SUBSYSTEM_STATUS_KEY]
