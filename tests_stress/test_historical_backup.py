"""Frozen release-schema backup migration through genuine HA restore and Assist."""

from __future__ import annotations

import json
from pathlib import Path

from pytest_homeassistant_custom_component.common import MockConfigEntry, MockUser

from custom_components.extended_openai_conversation_responses import backup
from custom_components.extended_openai_conversation_responses.const import (
    CONF_SKIP_AUTHENTICATION,
    CONFIG_ENTRY_VERSION,
    DOMAIN,
)
from homeassistant.components import conversation
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from tests_stress.conftest import record
from tests_stress.health import HealthChecks, assert_enhanced_health

FIXTURE = Path(__file__).parent / "fixtures" / "backup-v5.3.0-format2.json"


async def test_tagged_format_two_fixture_imports_and_reexports(
    hass: HomeAssistant,
    monkeypatch,
    stress_trace: list[dict],
) -> None:
    historical = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert historical["integration_version"] == "5.3.0"
    assert historical["version"] == 2
    MockUser(id="historical-owner", name="Historical owner", is_owner=True).add_to_hass(
        hass
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Historical target",
        data={CONF_API_KEY: "sk-local", CONF_SKIP_AUTHENTICATION: True},
        version=CONFIG_ENTRY_VERSION,
        subentries_data=[
            {
                "data": {},
                "subentry_type": "conversation",
                "title": "Restore target",
                "unique_id": None,
            }
        ],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    subentry = next(iter(entry.subentries.values()))
    prepared = backup.inspect_backup(historical, subentry.subentry_id)
    assert len(prepared.memories) == 1
    assert len(prepared.knowledge) == 1
    assert prepared.request_rules["rules"] == []
    assert (await backup.async_restore_backup(hass, entry, subentry, historical))[
        "status"
    ] == "restored"
    await hass.async_block_till_done()
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    current = await backup.async_collect_backup_snapshot(hass, entry, subentry)
    assert current["version"] == backup.BACKUP_VERSION
    assert "request_rules" in current
    assert current["memories"]["memories"][0]["content"] == (
        "The kitchen light is named Aurora"
    )
    assert current["knowledge"]["sources"][0]["content"] == (
        "The stopcock is under the kitchen sink."
    )
    assert (await backup.async_restore_backup(hass, entry, subentry, current))[
        "status"
    ] == "restored"
    await hass.async_block_till_done()
    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert agent is not None

    async def model(log, **kwargs):
        del kwargs
        log.async_add_assistant_content_without_tools(
            conversation.AssistantContent(
                agent_id=agent.entity_id, content="historical request healthy"
            )
        )

    monkeypatch.setattr(agent, "_async_handle_chat_log", model)
    counts = await assert_enhanced_health(
        hass,
        entry,
        subentry,
        HealthChecks(
            backup=True,
            memory_users=("historical-owner",),
            knowledge=True,
            request_rules=True,
            public_probe=True,
            probe_user="historical-owner",
            expected_speech="historical request healthy",
        ),
    )
    assert counts["memory_records"] == 1
    assert counts["knowledge_sources"] == 1
    record(
        stress_trace,
        "summary",
        layer="real-ha",
        historical_fixtures=1,
        import_round_trips=2,
        public_turns=1,
    )
