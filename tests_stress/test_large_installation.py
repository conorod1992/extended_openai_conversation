"""Large valid Home Assistant installation with management and public paths."""

from __future__ import annotations

from time import perf_counter

from pytest_homeassistant_custom_component.common import MockConfigEntry, MockUser

from custom_components.extended_openai_conversation_responses import backup
from custom_components.extended_openai_conversation_responses.const import (
    CONF_FUNCTION_GROUPS,
    CONF_FUNCTION_TOOLS,
    CONF_KNOWLEDGE_ENABLED,
    CONF_MEMORY_MODE,
    CONF_SKIP_AUTHENTICATION,
    CONFIG_ENTRY_VERSION,
    DOMAIN,
    MEMORY_MODE_MANUAL,
)
from custom_components.extended_openai_conversation_responses.knowledge import (
    async_get_knowledge,
)
from custom_components.extended_openai_conversation_responses.memory import (
    async_get_memory,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    async_get_request_rules,
)
from homeassistant.components import conversation
from homeassistant.const import CONF_API_KEY
from homeassistant.core import Context, HomeAssistant
from tests_stress.conftest import record
from tests_stress.health import HealthChecks, assert_enhanced_health
from tests_stress.test_function_groups_state_machine import _tool


async def test_large_installation_survives_setup_management_backup_and_assist(
    hass: HomeAssistant,
    monkeypatch,
    stress_scale: int,
    stress_trace: list[dict],
) -> None:
    agents = 10 if stress_scale == 1 else 20
    rule_count = 100 * stress_scale
    tool_count = 60 * stress_scale
    group_count = 20 * stress_scale
    memory_count = 500 * stress_scale
    knowledge_count = 150 * stress_scale
    MockUser(
        id="large-owner", name="Large installation owner", is_owner=True
    ).add_to_hass(hass)
    tools = [_tool(f"large-tool-{number}") for number in range(tool_count)]
    groups = [
        {
            "id": f"large-group-{number}",
            "name": f"Large group {number}",
            "description": "Scale fixture",
            "loading_mode": "always" if number % 2 == 0 else "on_demand",
            "functions": [f"large-tool-{number * 3 + offset}" for offset in range(3)],
            "enabled": True,
        }
        for number in range(group_count)
    ]
    entries = []
    setup_started = perf_counter()
    for number in range(agents):
        options = {CONF_MEMORY_MODE: MEMORY_MODE_MANUAL, CONF_KNOWLEDGE_ENABLED: True}
        if number == 0:
            options |= {CONF_FUNCTION_TOOLS: tools, CONF_FUNCTION_GROUPS: groups}
        entry = MockConfigEntry(
            domain=DOMAIN,
            title=f"Scale provider {number}",
            data={CONF_API_KEY: "sk-local", CONF_SKIP_AUTHENTICATION: True},
            version=CONFIG_ENTRY_VERSION,
            subentries_data=[
                {
                    "data": options,
                    "subentry_type": "conversation",
                    "title": f"Scale agent {number}",
                    "unique_id": None,
                }
            ],
        )
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        entries.append(entry)
    await hass.async_block_till_done()
    setup_seconds = round(perf_counter() - setup_started, 3)
    primary = entries[0]
    subentry = next(iter(primary.subentries.values()))
    memory = await async_get_memory(hass, primary.entry_id, subentry.subentry_id)
    knowledge = await async_get_knowledge(hass, primary.entry_id, subentry.subentry_id)
    rules = await async_get_request_rules(hass, primary.entry_id, subentry.subentry_id)
    population_started = perf_counter()
    for number in range(memory_count):
        assert (
            await memory.async_add(
                "large-owner",
                f"Large memory {number} 🎯",
                "scale",
                "explicit",
                key=f"large-{number}",
            )
        )["status"] == "created"
    for number in range(knowledge_count):
        await knowledge.async_create(
            f"Large source {number}",
            f"Description {number}",
            f"Knowledge body {number} 東京",
        )
    for number in range(rule_count):
        await rules.async_create(
            {
                "name": f"Large rule {number}",
                "phrases": [f"large command {number}"],
                "match_type": "equals",
                "action_type": "local_action",
                "action": {"actions": [{"action": "script.turn_on"}]},
            }
        )
    population_seconds = round(perf_counter() - population_started, 3)
    counts = await assert_enhanced_health(
        hass,
        primary,
        subentry,
        HealthChecks(
            backup=True,
            memory_users=("large-owner",),
            knowledge=True,
            request_rules=True,
        ),
    )
    assert counts["memory_records"] == memory_count
    assert counts["knowledge_sources"] == knowledge_count
    assert counts["request_rules"] == rule_count
    snapshot_started = perf_counter()
    snapshot = await backup.async_collect_backup_snapshot(hass, primary, subentry)
    assert backup.inspect_backup(snapshot, subentry.subentry_id)
    snapshot_seconds = round(perf_counter() - snapshot_started, 3)
    assert await hass.config_entries.async_reload(primary.entry_id)
    await hass.async_block_till_done()
    assert (await backup.async_collect_backup_snapshot(hass, primary, subentry))[
        "memories"
    ] == snapshot["memories"]
    for entry in entries:
        agent = conversation.async_get_agent(hass, entry.entry_id)
        assert agent is not None

        async def model(log, *, entity_id=agent.entity_id, **kwargs):
            del kwargs
            log.async_add_assistant_content_without_tools(
                conversation.AssistantContent(
                    agent_id=entity_id, content="scale healthy"
                )
            )

        monkeypatch.setattr(agent, "_async_handle_chat_log", model)
        result = await conversation.async_converse(
            hass=hass,
            text="scale public probe",
            conversation_id=None,
            context=Context(user_id="large-owner"),
            language="en",
            agent_id=entry.entry_id,
        )
        assert result.response.as_dict()["speech"]["plain"]["speech"] == "scale healthy"
    record(
        stress_trace,
        "summary",
        layer="real-ha",
        agents=agents,
        request_rules=rule_count,
        function_tools=tool_count,
        function_groups=group_count,
        memory_records=memory_count,
        knowledge_sources=knowledge_count,
        public_turns=agents,
        setup_seconds=setup_seconds,
        population_seconds=population_seconds,
        snapshot_seconds=snapshot_seconds,
        reloads=1,
    )
