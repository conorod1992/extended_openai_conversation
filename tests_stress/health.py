"""Optional, reusable invariants for stateful enhanced acceptance campaigns."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from custom_components.extended_openai_conversation_responses import backup
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
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import Context, HomeAssistant


@dataclass(frozen=True)
class HealthChecks:
    backup: bool = False
    memory_users: tuple[str, ...] = ()
    knowledge: bool = False
    request_rules: bool = False
    public_probe: bool = False
    probe_user: str | None = None
    probe_text: str = "enhanced health probe"
    expected_speech: str | None = None


async def assert_enhanced_health(
    hass: HomeAssistant,
    entry: Any,
    subentry: Any,
    checks: HealthChecks,
) -> dict[str, int]:
    """Check public registration and selected durable invariants after a mutation.

    Optional checks keep the helper usable by campaigns with only some subsystems
    initialized. The public probe deliberately uses the real HA Assist entry point;
    callers arrange the deterministic local provider response they expect.
    """
    assert entry.state is ConfigEntryState.LOADED
    assert subentry.subentry_id in entry.subentries
    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert agent is not None
    assert agent.entry.entry_id == entry.entry_id
    counts: dict[str, int] = {"registered_agents": 1}

    if checks.backup:
        snapshot = await backup.async_collect_backup_snapshot(hass, entry, subentry)
        assert backup.inspect_backup(snapshot, subentry.subentry_id)
        counts["backup_subsystems"] = len(snapshot) - 4

    if checks.memory_users:
        memory = await async_get_memory(hass, entry.entry_id, subentry.subentry_id)
        seen: set[str] = set()
        total = 0
        for user_id in checks.memory_users:
            records = []
            offset = 0
            while True:
                page, more = await memory.async_list_page(
                    user_id, limit=100, offset=offset
                )
                records.extend(page)
                offset += len(page)
                if not more:
                    break
            assert all(item.user_id == user_id for item in records)
            identifiers = {item.memory_id for item in records}
            assert len(identifiers) == len(records)
            assert not (seen & identifiers)
            seen.update(identifiers)
            total += len(records)
        counts["memory_records"] = total

    if checks.knowledge:
        knowledge = await async_get_knowledge(
            hass, entry.entry_id, subentry.subentry_id
        )
        sources = await knowledge.async_list()
        assert len({source["source_id"] for source in sources}) == len(sources)
        counts["knowledge_sources"] = len(sources)

    if checks.request_rules:
        rules = await async_get_request_rules(
            hass, entry.entry_id, subentry.subentry_id
        )
        rule_items = rules.snapshot()["rules"]
        assert len({rule["id"] for rule in rule_items}) == len(rule_items)
        assert len({rule["order"] for rule in rule_items}) == len(rule_items)
        counts["request_rules"] = len(rule_items)

    if checks.public_probe:
        result = await conversation.async_converse(
            hass=hass,
            text=checks.probe_text,
            conversation_id=None,
            context=Context(user_id=checks.probe_user),
            language="en",
            agent_id=entry.entry_id,
        )
        assert result.response.error_code is None
        if checks.expected_speech is not None:
            assert (
                result.response.as_dict()["speech"]["plain"]["speech"]
                == checks.expected_speech
            )
        counts["public_turns"] = 1
    return counts
