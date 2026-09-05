"""Focused regressions for PR19 management state and backup integrity."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses.agent_config import (
    MAX_AGENT_TITLE_LENGTH,
    AgentConfigError,
    agent_config_defaults,
    validate_agent_title,
)
from custom_components.extended_openai_conversation_responses.const import DOMAIN
from custom_components.extended_openai_conversation_responses.management_ui import (
    _agent_config_revision,
    _parse_import_document,
    async_management_command,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    DEFAULT_MATCHING,
    RequestRules,
)
from custom_components.extended_openai_conversation_responses.secret_redaction import (
    REDACTED_SECRET_SENTINEL,
    redact_secrets,
    restore_redacted_secrets,
)
from homeassistant.exceptions import HomeAssistantError


class MemoryStore:
    """Minimal in-memory Store seam."""

    def __init__(self, data=None):
        self.data = deepcopy(data)

    async def async_load(self):
        return deepcopy(self.data)

    async def async_save(self, data):
        self.data = deepcopy(data)


def local_rule(rule_id: str = "rule-1") -> dict:
    return {
        "id": rule_id,
        "name": "Good night",
        "enabled": True,
        "phrases": ["good night"],
        "match_type": "equals",
        "action_type": "local_action",
        "action": {
            "actions": [
                {
                    "domain": "script",
                    "service": "turn_on",
                    "target": {"entity_id": ["script.goodnight"]},
                    "data": {},
                }
            ],
            "success_response": "Done",
            "failure_response": "Failed",
        },
        "matching_behavior": "defaults",
        "matching": dict(DEFAULT_MATCHING),
        "order": 0,
    }


def setup_entry(hass):
    subentry = SimpleNamespace(
        subentry_id="agent-1",
        subentry_type="conversation",
        title="Jarvis",
        data=agent_config_defaults(),
    )
    entry = SimpleNamespace(
        entry_id="entry-1",
        domain=DOMAIN,
        title="Provider",
        data={},
        subentries={subentry.subentry_id: subentry},
    )
    hass.config_entries.async_get_entry.return_value = entry
    return entry, subentry


def test_agent_title_contract_is_shared_and_bounded() -> None:
    assert validate_agent_title("  Jarvis  ") == "Jarvis"
    assert validate_agent_title(None, default="Imported conversation agent") == (
        "Imported conversation agent"
    )
    assert validate_agent_title("x" * MAX_AGENT_TITLE_LENGTH) == (
        "x" * MAX_AGENT_TITLE_LENGTH
    )
    with pytest.raises(AgentConfigError, match="must not be empty"):
        validate_agent_title("   ")
    with pytest.raises(AgentConfigError, match="at most 255"):
        validate_agent_title("x" * (MAX_AGENT_TITLE_LENGTH + 1))


def test_import_uses_title_contract_and_removes_redaction_sentinel() -> None:
    config = agent_config_defaults()
    config["voice_device_mappings"] = {"device": None}
    # Ordinary null values are not confused with the explicit credential sentinel.
    assert restore_redacted_secrets(config)["voice_device_mappings"]["device"] is None

    valid_config = agent_config_defaults()
    # Export/backup redaction can leave a sentinel under a credential-like key.
    # Import must remove that field before normal config validation sees it.
    valid_config["api_key"] = REDACTED_SECRET_SENTINEL
    parsed = _parse_import_document(
        {
            "schema": "extended_openai_conversation.agent",
            "version": 1,
            "title": "  Imported  ",
            "config": valid_config,
        }
    )
    assert parsed["title"] == "Imported"
    assert "api_key" not in parsed["config"]

    with pytest.raises(AgentConfigError, match="must not be empty"):
        _parse_import_document(
            {
                "schema": "extended_openai_conversation.agent",
                "version": 1,
                "title": "   ",
                "config": agent_config_defaults(),
            }
        )


def test_redaction_preserves_structure_but_restore_drops_only_sentinel() -> None:
    original = {
        "actions": [
            {
                "action": "rest.call",
                "data": {
                    "Authorization": "Bearer secret",
                    "payload": None,
                    "nested": [None, {"api-key": "credential"}],
                },
            }
        ],
        "parameters": {
            "type": "object",
            "properties": {"api_key": {"type": "string"}},
        },
    }
    redacted = redact_secrets(original)
    data = redacted["actions"][0]["data"]
    assert data["Authorization"] == REDACTED_SECRET_SENTINEL
    assert data["nested"][1]["api-key"] == REDACTED_SECRET_SENTINEL
    assert "api_key" in redacted["parameters"]["properties"]

    restored = restore_redacted_secrets(redacted)
    restored_data = restored["actions"][0]["data"]
    assert "Authorization" not in restored_data
    assert restored_data["payload"] is None
    assert restored_data["nested"] == [None, {}]


async def test_stale_agent_configuration_revision_is_rejected(hass) -> None:
    _entry, subentry = setup_entry(hass)
    get_message = {
        "section": "configuration",
        "action": "get",
        "entry_id": "entry-1",
        "subentry_id": "agent-1",
    }
    snapshot = await async_management_command(hass, "admin", True, get_message)
    assert snapshot["revision"] == _agent_config_revision(subentry.data, subentry.title)

    subentry.data = {**subentry.data, "max_tokens": 900}
    with pytest.raises(HomeAssistantError, match="changed in another tab"):
        await async_management_command(
            hass,
            "admin",
            True,
            {
                **get_message,
                "action": "update",
                "config": {"max_tokens": 700},
                "revision": snapshot["revision"],
            },
        )
    hass.config_entries.async_update_subentry.assert_not_called()


async def test_request_rule_revision_rejects_stale_writer_and_is_not_backed_up() -> None:
    rules = RequestRules(MemoryStore({"rules": [local_rule()]}))
    await rules.async_initialize()
    first = rules.snapshot()["revision"]

    await rules.async_set_defaults(
        {**DEFAULT_MATCHING, "fuzzy": True}, expected_revision=first
    )
    current = rules.snapshot()["revision"]
    assert current != first

    with pytest.raises(ValueError, match="changed in another tab"):
        await rules.async_delete("rule-1", expected_revision=first)
    assert rules.snapshot()["revision"] == current
    assert "revision" not in await rules.async_backup_data()
