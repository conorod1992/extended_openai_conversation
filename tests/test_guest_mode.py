"""Focused tests for backend-enforced Guest Mode."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses.agent_config import (
    AgentConfigError,
    agent_config_defaults,
    validate_function_tools,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_ARCHIVE_ENABLED,
    CONF_GUEST_ALLOWED_GROUP_IDS,
    CONF_GUEST_CONTROL_EXCLUDED_AREAS,
    CONF_GUEST_CONTROL_EXCLUDED_DOMAINS,
    CONF_GUEST_CONTROL_EXCLUDED_ENTITIES,
    CONF_GUEST_CONTROL_EXCLUDED_LABELS,
    CONF_GUEST_CONTROLLABLE_DOMAINS,
    CONF_GUEST_EXCLUDED_AREAS,
    CONF_GUEST_EXCLUDED_DOMAINS,
    CONF_GUEST_EXCLUDED_ENTITIES,
    CONF_GUEST_EXCLUDED_LABELS,
    CONF_GUEST_FUNCTION_POLICY,
    CONF_GUEST_KNOWLEDGE_ENABLED,
    CONF_GUEST_MODE_ENABLED,
    CONF_GUEST_POLICY_VERSION,
    CONF_GUEST_READABLE_DOMAINS,
    CONF_GUEST_SEPARATE_CONTROL_RESTRICTIONS,
    CONF_GUEST_SHARED_MEMORY_POLICY,
    CONF_GUEST_SHARED_MEMORY_READ,
    CONF_GUEST_SHARED_MEMORY_WRITE,
    CONF_KNOWLEDGE_ENABLED,
    CONF_MEMORY_MODE,
    CONF_PROMPT,
    CONF_SHARED_MEMORY_MODE,
    CONF_TEMPORARY_MEMORY,
    GUEST_POLICY_VERSION,
    MEMORY_MODE_AUTOMATIC,
    SERVICE_GUEST_MODE_DISABLE,
    SERVICE_GUEST_MODE_UPDATE,
    SHARED_MEMORY_AUTOMATIC,
    TEMPORARY_MEMORY_BALANCED,
)
from custom_components.extended_openai_conversation_responses.conversation import (
    _ACTIVE_GUEST_POLICY,
    ExtendedOpenAIAgentEntity,
)
from custom_components.extended_openai_conversation_responses.guest_mode import (
    GUEST_MODE_PROMPT,
    GuestCapabilityPolicy,
    GuestModeManager,
    resolve_guest_policy,
)
from custom_components.extended_openai_conversation_responses.prompt import (
    render_effective_prompt,
)
from custom_components.extended_openai_conversation_responses.request import (
    assemble_integration_function_tools,
)
from custom_components.extended_openai_conversation_responses.services import (
    async_setup_services,
)
from homeassistant.helpers import device_registry as dr, entity_registry as er


def _time(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=UTC)


def _manager(hass) -> GuestModeManager:
    manager = GuestModeManager(hass, "entry", "agent")
    manager._store = SimpleNamespace(async_save=AsyncMock(), async_load=AsyncMock())
    manager._initialized = True
    return manager


async def test_trusted_interval_states_and_disable(hass) -> None:
    manager = _manager(hass)
    now = _time("2026-08-21T12:00:00")
    scheduled = await manager.async_update_trusted(
        active_from="2026-08-22T12:00:00+00:00",
        active_until="2026-08-23T12:00:00+00:00",
        now=now,
    )
    assert scheduled["state"] == "scheduled"
    assert manager.status(_time("2026-08-22T18:00:00"))["state"] == "active"
    assert manager.status(_time("2026-08-24T12:00:00"))["state"] == "inactive"

    await manager.async_update_trusted(indefinite=True, now=now)
    assert manager.status(now)["state"] == "active_indefinitely"
    assert (await manager.async_disable_trusted())["state"] == "inactive"


async def test_llm_restriction_is_monotonic(hass) -> None:
    manager = _manager(hass)
    now = _time("2026-08-21T12:00:00")
    await manager.async_update_trusted(
        active_from="2026-08-22T12:00:00+00:00",
        active_until="2026-08-23T12:00:00+00:00",
        now=now,
    )

    await manager.async_restrict(
        active_from="2026-08-22T18:00:00+00:00",
        active_until="2026-08-23T06:00:00+00:00",
        now=now,
    )
    assert manager.schedule.active_from == "2026-08-22T12:00:00+00:00"
    assert manager.schedule.active_until == "2026-08-23T12:00:00+00:00"

    await manager.async_restrict(
        active_from="2026-08-22T06:00:00+00:00",
        active_until="2026-08-24T12:00:00+00:00",
        now=now,
    )
    assert manager.schedule.active_from == "2026-08-22T06:00:00+00:00"
    assert manager.schedule.active_until == "2026-08-24T12:00:00+00:00"

    await manager.async_restrict(make_indefinite=True, now=now)
    assert manager.schedule.active_until is None
    await manager.async_restrict(active_until="2026-08-25T12:00:00+00:00", now=now)
    assert manager.schedule.active_until is None


def test_resolved_policy_intersects_read_and_control(hass, monkeypatch) -> None:
    manager = _manager(hass)
    manager._schedule = SimpleNamespace(
        active_from="2026-01-01T00:00:00+00:00",
        active_until=None,
        source="test",
        updated_at=None,
    )
    hass.states.async_all.return_value = [
        SimpleNamespace(entity_id="light.guest"),
        SimpleNamespace(entity_id="lock.private"),
    ]
    monkeypatch.setattr(
        er,
        "async_get",
        lambda _hass: SimpleNamespace(async_get=lambda _entity_id: None),
    )
    monkeypatch.setattr(
        dr,
        "async_get",
        lambda _hass: SimpleNamespace(async_get=lambda _device_id: None),
    )
    options = {
        CONF_GUEST_READABLE_DOMAINS: ["light"],
        CONF_GUEST_CONTROLLABLE_DOMAINS: ["light", "lock"],
        CONF_GUEST_SHARED_MEMORY_READ: True,
        CONF_GUEST_SHARED_MEMORY_WRITE: False,
        CONF_GUEST_KNOWLEDGE_ENABLED: True,
    }
    tools = [
        {"guest_allowed": True, "spec": {"name": "safe"}},
        {"spec": {"name": "owner_only"}},
    ]
    policy = resolve_guest_policy(hass, options, manager, tools)

    assert policy.readable_entity_ids == frozenset({"light.guest"})
    assert policy.controllable_entity_ids == frozenset({"light.guest"})
    assert policy.configured_tool_names == frozenset({"safe"})

    assert policy.personal_memory_read is False
    assert policy.shared_memory_read is True
    assert policy.shared_memory_write is False
    assert policy.archive_retention is False
    assert policy.temporary_memory is False
    assert policy.knowledge_access is True
    assert policy.web_search is False


def test_disabling_controls_does_not_weaken_active_guest_policy(
    hass, monkeypatch
) -> None:
    manager = _manager(hass)
    monkeypatch.setattr(manager, "is_active", lambda: True)
    hass.states.async_all.return_value = []
    monkeypatch.setattr(
        er,
        "async_get",
        lambda _hass: SimpleNamespace(async_get=lambda _entity_id: None),
    )
    monkeypatch.setattr(
        dr,
        "async_get",
        lambda _hass: SimpleNamespace(async_get=lambda _device_id: None),
    )

    policy = resolve_guest_policy(
        hass,
        {CONF_GUEST_MODE_ENABLED: False},
        manager,
    )

    assert policy.guest_active is True
    assert policy.archive_retention is False
    assert policy.personal_memory_read is False


def test_guest_integration_tools_are_filtered() -> None:
    options = agent_config_defaults()
    options.update(
        {
            CONF_GUEST_MODE_ENABLED: True,
            CONF_MEMORY_MODE: MEMORY_MODE_AUTOMATIC,
            CONF_SHARED_MEMORY_MODE: SHARED_MEMORY_AUTOMATIC,
            CONF_TEMPORARY_MEMORY: TEMPORARY_MEMORY_BALANCED,
            CONF_ARCHIVE_ENABLED: True,
            CONF_KNOWLEDGE_ENABLED: True,
            CONF_PROMPT: "You are helpful.",
        }
    )
    policy = GuestCapabilityPolicy(
        True,
        shared_memory_read=True,
        shared_memory_write=False,
        archive_access=False,
        archive_retention=False,
        knowledge_access=False,
        temporary_memory=False,
        skills=False,
        web_search=False,
    )
    tools = assemble_integration_function_tools(
        options,
        set(),
        memory_scope_available=True,
        temporary_scope_available=True,
        knowledge_available=True,
        guest_policy=policy,
    )
    names = {tool["spec"]["name"] for tool in tools}
    assert names == {"memory_search", "memory_list", "guest_mode_restrict"}


def test_guest_prompt_omits_private_capability_sections(hass) -> None:
    options = agent_config_defaults()
    options.update(
        {
            CONF_MEMORY_MODE: MEMORY_MODE_AUTOMATIC,
            CONF_TEMPORARY_MEMORY: TEMPORARY_MEMORY_BALANCED,
            CONF_ARCHIVE_ENABLED: True,
            CONF_KNOWLEDGE_ENABLED: True,
            CONF_PROMPT: "You are helpful.",
        }
    )
    user_input = SimpleNamespace(text="hello")
    policy = GuestCapabilityPolicy(
        True,
        shared_memory_read=False,
        shared_memory_write=False,
        archive_access=False,
        archive_retention=False,
        knowledge_access=False,
        temporary_memory=False,
        skills=False,
        web_search=False,
    )
    rendered = render_effective_prompt(
        hass,
        options,
        exposed_entities=[],
        current_device_id=None,
        user_input=user_input,
        skills=[],
        knowledge_available=True,
        guest_policy=policy,
    )
    keys = {section.key for section in rendered.sections}
    assert GUEST_MODE_PROMPT.strip() in rendered.text
    assert "persistent_memory_instructions" not in keys
    assert "temporary_memory_instructions" not in keys
    assert "archive_instructions" not in keys
    assert "knowledge_instructions" not in keys


def test_execution_argument_guard_blocks_hidden_and_broad_targets() -> None:
    policy = GuestCapabilityPolicy(
        True,
        readable_entity_ids=frozenset({"light.guest"}),
        controllable_entity_ids=frozenset({"light.guest"}),
    )
    allowed = ExtendedOpenAIAgentEntity._guest_arguments_allowed
    assert allowed({"entity_id": "light.guest"}, policy, control=True)
    assert not allowed({"entity_id": "lock.private"}, policy, control=True)
    assert not allowed({"area_id": "whole_house"}, policy, control=True)
    assert not allowed(
        {"statistic_ids": ["sensor.private_energy"]}, policy, control=False
    )
    assert not allowed(
        {"list": [{"service_data": {"entity_id": "light.guest, lock.private"}}]},
        policy,
        control=True,
    )
    assert ExtendedOpenAIAgentEntity._is_guest_unscopable_tool(
        {"function": {"type": "native", "name": "get_energy"}}
    )


@pytest.mark.parametrize(
    ("selector_key", "selector_value", "selection_attribute"),
    [
        ("area_id", "kitchen", "area_ids"),
        ("device_id", "kitchen-device", "device_ids"),
        ("label_id", "kitchen-label", "label_ids"),
    ],
)
def test_runtime_broad_target_denies_if_any_ha_resolved_entity_is_forbidden(
    hass, monkeypatch, selector_key, selector_value, selection_attribute
) -> None:
    entity = object.__new__(ExtendedOpenAIAgentEntity)
    entity.hass = hass

    def resolve(_hass, selection):
        assert getattr(selection, selection_attribute) == {selector_value}
        return SimpleNamespace(
            referenced=set(),
            indirectly_referenced={"light.kitchen_ceiling", "light.kitchen_cabinet"},
        )

    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.guest_mode.target_helpers.async_extract_referenced_entity_ids",
        resolve,
    )
    policy = GuestCapabilityPolicy(
        True,
        readable_entity_ids=frozenset({"light.kitchen_ceiling"}),
        controllable_entity_ids=frozenset({"light.kitchen_ceiling"}),
    )
    assert not entity._guest_arguments_allowed_runtime(
        {selector_key: selector_value}, policy, control=True
    )


@pytest.mark.parametrize(
    ("selector_key", "selector_value"),
    [
        ("area_id", "kitchen"),
        ("device_id", "kitchen-device"),
        ("label_id", "kitchen-label"),
    ],
)
def test_runtime_broad_target_allows_only_when_every_resolved_entity_is_allowed(
    hass, monkeypatch, selector_key, selector_value
) -> None:
    entity = object.__new__(ExtendedOpenAIAgentEntity)
    entity.hass = hass
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.guest_mode.target_helpers.async_extract_referenced_entity_ids",
        lambda _hass, _selection: SimpleNamespace(
            referenced=set(),
            indirectly_referenced={"light.one", "light.two"},
        ),
    )
    policy = GuestCapabilityPolicy(
        True,
        readable_entity_ids=frozenset({"light.one", "light.two"}),
        controllable_entity_ids=frozenset({"light.one", "light.two"}),
    )
    assert entity._guest_arguments_allowed_runtime(
        {selector_key: selector_value}, policy, control=True
    )


def test_runtime_direct_entity_and_inactive_guest_behavior(hass, monkeypatch) -> None:
    entity = object.__new__(ExtendedOpenAIAgentEntity)
    entity.hass = hass
    policy = GuestCapabilityPolicy(
        True,
        readable_entity_ids=frozenset({"light.guest"}),
        controllable_entity_ids=frozenset({"light.guest"}),
    )
    assert entity._guest_arguments_allowed_runtime(
        {"entity_id": "light.guest"}, policy, control=True
    )
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.guest_mode.target_helpers.async_extract_referenced_entity_ids",
        lambda *_args: pytest.fail("inactive Guest Mode must not resolve targets"),
    )
    assert entity._guest_arguments_allowed_runtime(
        {"area_id": "whole-house"},
        GuestCapabilityPolicy.unrestricted(),
        control=True,
    )


def test_runtime_broad_target_denies_when_ha_resolves_no_entities(
    hass, monkeypatch
) -> None:
    entity = object.__new__(ExtendedOpenAIAgentEntity)
    entity.hass = hass
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.guest_mode.target_helpers.async_extract_referenced_entity_ids",
        lambda _hass, _selection: SimpleNamespace(
            referenced=set(), indirectly_referenced=set()
        ),
    )
    assert not entity._guest_arguments_allowed_runtime(
        {"label_id": "missing-label"},
        GuestCapabilityPolicy(True),
        control=True,
    )


def test_mid_request_activation_is_pinned_until_turn_ends(monkeypatch) -> None:
    entity = object.__new__(ExtendedOpenAIAgentEntity)
    active = GuestCapabilityPolicy(
        True,
        readable_entity_ids=frozenset(),
        controllable_entity_ids=frozenset(),
    )
    live = iter([active, GuestCapabilityPolicy.unrestricted()])
    monkeypatch.setattr(entity, "_resolve_live_guest_policy", lambda: next(live))
    token = _ACTIVE_GUEST_POLICY.set(GuestCapabilityPolicy.unrestricted())
    try:
        assert entity._effective_guest_policy().guest_active is True
        assert entity._effective_guest_policy().guest_active is True
    finally:
        _ACTIVE_GUEST_POLICY.reset(token)


def test_group_and_tool_guest_flags_are_both_required() -> None:
    tools = [
        {"guest_allowed": True, "spec": {"name": "grouped"}},
        {"guest_allowed": True, "spec": {"name": "ungrouped"}},
    ]
    owner_only_group = {
        "id": "private",
        "name": "Private",
        "description": "Private",
        "loading_mode": "always",
        "functions": ["grouped"],
    }
    policy = GuestCapabilityPolicy(
        True,
        configured_tool_names=frozenset({"grouped", "ungrouped"}),
        legacy_function_flags=True,
    )
    filtered, groups = ExtendedOpenAIAgentEntity._filter_guest_tools_and_groups(
        tools, [owner_only_group], policy
    )
    assert {tool["spec"]["name"] for tool in filtered} == {"ungrouped"}
    assert groups == []

    owner_only_group["guest_allowed"] = True
    filtered, groups = ExtendedOpenAIAgentEntity._filter_guest_tools_and_groups(
        tools, [owner_only_group], policy
    )
    assert {tool["spec"]["name"] for tool in filtered} == {
        "grouped",
        "ungrouped",
    }
    assert groups[0]["functions"] == ["grouped"]


def test_v2_exclusions_union_and_control_is_subset(hass, monkeypatch) -> None:
    manager = _manager(hass)
    monkeypatch.setattr(manager, "is_active", lambda: True)
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.guest_mode.get_exposed_entities",
        lambda _hass: [
            {"entity_id": "light.kitchen"},
            {"entity_id": "light.hall"},
            {"entity_id": "lock.front"},
        ],
    )
    monkeypatch.setattr(
        er, "async_get", lambda _hass: SimpleNamespace(async_get=lambda _id: None)
    )
    monkeypatch.setattr(
        dr, "async_get", lambda _hass: SimpleNamespace(async_get=lambda _id: None)
    )
    policy = resolve_guest_policy(
        hass,
        {
            CONF_GUEST_POLICY_VERSION: GUEST_POLICY_VERSION,
            CONF_GUEST_EXCLUDED_DOMAINS: ["lock"],
            CONF_GUEST_SEPARATE_CONTROL_RESTRICTIONS: True,
            CONF_GUEST_CONTROL_EXCLUDED_DOMAINS: ["light"],
            CONF_GUEST_FUNCTION_POLICY: "off",
            CONF_GUEST_SHARED_MEMORY_POLICY: "off",
        },
        manager,
    )
    assert policy.readable_entity_ids == frozenset({"light.kitchen", "light.hall"})
    assert policy.controllable_entity_ids == frozenset()
    assert policy.controllable_entity_ids <= policy.readable_entity_ids


def test_v2_function_on_still_denies_unsafe_native(hass, monkeypatch) -> None:
    manager = _manager(hass)
    monkeypatch.setattr(manager, "is_active", lambda: True)
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.guest_mode.get_exposed_entities",
        lambda _hass: [],
    )
    tools = [
        {
            "spec": {"name": "safe"},
            "function": {"type": "native", "name": "execute_service"},
        },
        {
            "spec": {"name": "energy"},
            "function": {"type": "native", "name": "get_energy"},
        },
    ]
    policy = resolve_guest_policy(
        hass,
        {
            CONF_GUEST_POLICY_VERSION: GUEST_POLICY_VERSION,
            CONF_GUEST_FUNCTION_POLICY: "on",
        },
        manager,
        tools,
    )
    assert policy.configured_tool_names == frozenset({"safe"})

    custom = resolve_guest_policy(
        hass,
        {
            CONF_GUEST_POLICY_VERSION: GUEST_POLICY_VERSION,
            CONF_GUEST_FUNCTION_POLICY: "custom",
            CONF_GUEST_ALLOWED_GROUP_IDS: ["safe_group"],
            "function_groups": [
                {
                    "id": "safe_group",
                    "functions": ["safe", "energy"],
                }
            ],
        },
        manager,
        tools,
    )
    assert custom.configured_tool_names == frozenset({"safe"})


def test_new_guest_defaults_enable_controls_with_private_capabilities() -> None:
    defaults = agent_config_defaults()
    assert defaults[CONF_GUEST_MODE_ENABLED] is True
    assert defaults[CONF_GUEST_POLICY_VERSION] == GUEST_POLICY_VERSION
    assert defaults[CONF_GUEST_FUNCTION_POLICY] == "off"
    assert defaults[CONF_GUEST_SHARED_MEMORY_POLICY] == "off"
    assert defaults[CONF_GUEST_SEPARATE_CONTROL_RESTRICTIONS] is False
    assert all(
        defaults[key] == []
        for key in (
            CONF_GUEST_EXCLUDED_LABELS,
            CONF_GUEST_EXCLUDED_AREAS,
            CONF_GUEST_EXCLUDED_DOMAINS,
            CONF_GUEST_EXCLUDED_ENTITIES,
            CONF_GUEST_CONTROL_EXCLUDED_LABELS,
            CONF_GUEST_CONTROL_EXCLUDED_AREAS,
            CONF_GUEST_CONTROL_EXCLUDED_DOMAINS,
            CONF_GUEST_CONTROL_EXCLUDED_ENTITIES,
        )
    )


async def test_custom_knowledge_filters_catalog_and_direct_ids() -> None:
    entity = object.__new__(ExtendedOpenAIAgentEntity)
    policy = GuestCapabilityPolicy(
        True,
        knowledge_access=True,
        knowledge_source_ids=frozenset({"allowed"}),
    )
    entity._effective_guest_policy = lambda: policy
    entity.subentry = SimpleNamespace(data={CONF_KNOWLEDGE_ENABLED: True})
    entity._knowledge = SimpleNamespace(
        source_count=2,
        resolve_source_filter=lambda source_ids: (None, list(source_ids or [])),
        async_search=AsyncMock(return_value=[{"private": True}]),
        async_catalog=AsyncMock(return_value={"sources": []}),
        async_get_section=AsyncMock(return_value={"content": "private"}),
    )

    await entity._async_execute_knowledge_tool("list", {})
    entity._knowledge.async_catalog.assert_awaited_once_with(
        None, 20, 0, frozenset({"allowed"})
    )
    with pytest.raises(RuntimeError, match="unavailable in Guest Mode"):
        await entity._async_execute_knowledge_tool("get", {"source_id": "forbidden"})
    entity._knowledge.async_get_section.assert_not_awaited()
    searched = await entity._async_execute_knowledge_tool("search", {"query": "secret"})
    assert searched["results"] == []
    entity._knowledge.async_search.assert_not_awaited()


def test_guest_allowed_metadata_defaults_false_and_validates() -> None:
    tool = {
        "spec": {
            "name": "safe",
            "description": "Safe",
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {"type": "native", "name": "get_energy"},
    }
    assert validate_function_tools([tool])[0].get("guest_allowed", False) is False
    tool["guest_allowed"] = "yes"
    with pytest.raises(AgentConfigError, match="guest_allowed"):
        validate_function_tools([tool])


def test_guest_schedule_backup_validation() -> None:
    schedule = GuestModeManager.validate_backup_data(
        {
            "schedule": {
                "active_from": "2026-08-22T12:00:00+00:00",
                "active_until": None,
                "source": "home_assistant",
                "updated_at": "2026-08-21T12:00:00+00:00",
            }
        }
    )
    assert schedule is not None
    assert schedule.active_until is None
    with pytest.raises(ValueError, match="Guest Mode"):
        GuestModeManager.validate_backup_data({"schedule": {"active_from": "bad"}})


async def test_trusted_services_update_and_disable_target_agent(
    hass, monkeypatch
) -> None:
    subentry = SimpleNamespace(
        subentry_id="agent-1", subentry_type="conversation", data={}
    )
    entry = SimpleNamespace(
        entry_id="entry-1",
        domain="extended_openai_conversation_responses",
        subentries={"agent-1": subentry},
    )
    hass.config_entries.async_get_entry.return_value = entry
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.services.er.async_get",
        lambda _hass: SimpleNamespace(async_get=lambda _entity_id: None),
    )
    manager = SimpleNamespace(
        async_update_trusted=AsyncMock(return_value={"state": "scheduled"}),
        async_disable_trusted=AsyncMock(return_value={"state": "inactive"}),
    )
    get_manager = AsyncMock(return_value=manager)
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.services.async_get_guest_mode",
        get_manager,
    )
    hass.auth.async_get_user.return_value = SimpleNamespace(is_admin=True)
    await async_setup_services(hass, {})
    handlers = {
        call.args[1]: call.args[2]
        for call in hass.services.async_register.call_args_list
        if call.args[0] == "extended_openai_conversation_responses"
    }
    context = SimpleNamespace(user_id="admin")
    updated = await handlers[SERVICE_GUEST_MODE_UPDATE](
        SimpleNamespace(
            context=context,
            data={
                "config_entry": "entry-1",
                "agent_id": "agent-1",
                "active_from": "2026-08-23T12:00:00+00:00",
                "indefinite": False,
            },
        )
    )
    disabled = await handlers[SERVICE_GUEST_MODE_DISABLE](
        SimpleNamespace(
            context=context,
            data={"config_entry": "entry-1", "agent_id": "agent-1"},
        )
    )
    assert updated["state"] == "scheduled"
    assert disabled["state"] == "inactive"
    get_manager.assert_awaited_with(hass, "entry-1", "agent-1")
