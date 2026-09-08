"""Tests for unified portable export and selective import/restore planning."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml

from custom_components.extended_openai_conversation_responses import backup, transfer
from custom_components.extended_openai_conversation_responses.agent_config import (
    agent_config_defaults,
)
from custom_components.extended_openai_conversation_responses.const import (
    AGENT_CONFIG_EXPORT_VERSION,
    CONF_FUNCTION_TOOLS,
    CONF_PROMPT,
    DOMAIN,
    SERVICE_CALL_FUNCTION,
)
from custom_components.extended_openai_conversation_responses.ha_llm_tools import (
    new_reference_tool,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    DEFAULT_MATCHING,
    DEFAULT_WORDING_GROUPS,
    RequestRules,
    validate_rule,
)
from custom_components.extended_openai_conversation_responses.secret_redaction import (
    REDACTED_SECRET_SENTINEL,
)
from tests.test_backup import _document


def _rules_backup(rules: list[dict] | None = None) -> dict:
    return {
        "storage_version": 1,
        "defaults": dict(DEFAULT_MATCHING),
        "wording_groups": [deepcopy(group) for group in DEFAULT_WORDING_GROUPS],
        "rules": rules or [],
    }


def _function_rule(function_name: str = "remember") -> dict:
    raw = {
        "id": "remember-rule",
        "name": "Remember this",
        "enabled": True,
        "phrases": ["remember this"],
        "match_type": "equals",
        "action_type": "local_action",
        "action": {
            "actions": [
                {
                    "action": f"{DOMAIN}.{SERVICE_CALL_FUNCTION}",
                    "data": {
                        "function": function_name,
                        "arguments": {"fact": "hello"},
                    },
                }
            ],
            "success_response": "Done",
            "failure_response": "Failed safely",
        },
        "matching_behavior": "defaults",
        "matching": dict(DEFAULT_MATCHING),
        "order": 0,
    }
    return validate_rule(raw, validate_sentence_pattern=False)


def _remember_tool() -> dict:
    return {
        "enabled": True,
        "spec": {
            "name": "remember",
            "description": "Remember a fact",
            "parameters": {
                "type": "object",
                "properties": {"fact": {"type": "string"}},
                "required": ["fact"],
                "additionalProperties": False,
            },
        },
        "function": {"type": "native", "name": "execute_service"},
    }


def _entry_and_subentry(config: dict | None = None):
    return (
        SimpleNamespace(entry_id="entry-1"),
        SimpleNamespace(
            subentry_id="agent-1",
            title="Jarvis",
            data=config or agent_config_defaults(),
        ),
    )


async def test_shareable_setup_reads_only_setup_state_and_round_trips_ha_llm_reference(
    monkeypatch,
) -> None:
    reference = {
        "type": "ha_llm",
        "source_type": "platform",
        "source_id": "homeassistant",
        "api_id": "assist",
        "tool_name": "GetLiveContext",
    }
    config = agent_config_defaults()
    config[CONF_FUNCTION_TOOLS] = [new_reference_tool(reference, set())]
    entry, subentry = _entry_and_subentry(config)
    rules = SimpleNamespace(async_backup_data=AsyncMock(return_value=_rules_backup()))

    async def request_rules(_hass, _entry_id, _subentry_id):
        return rules

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("shareable setup accessed private durable state")

    monkeypatch.setattr(transfer, "async_get_request_rules", request_rules)
    for name in (
        "async_get_memory",
        "async_get_temporary_memory",
        "async_get_knowledge",
        "async_get_archive",
        "async_get_usage",
        "async_get_guest_mode",
    ):
        monkeypatch.setattr(transfer, name, forbidden)

    result = await transfer.async_create_setup_export(
        SimpleNamespace(), entry, subentry
    )
    document = result["document"]
    assert set(document["sections"]) == transfer.SETUP_SECTIONS

    prepared = transfer.inspect_transfer(document, "target-agent")
    tools = yaml.safe_load(prepared.config[CONF_FUNCTION_TOOLS])
    assert tools[0]["function"] == reference
    assert prepared.request_rules == RequestRules.validate_backup_data(_rules_backup())


async def test_custom_export_reads_only_selected_sections(monkeypatch) -> None:
    entry, subentry = _entry_and_subentry()
    memory = SimpleNamespace(async_backup_data=AsyncMock(return_value={"memories": []}))

    async def get_memory(_hass, _entry_id, _subentry_id):
        return memory

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("unselected manager was accessed")

    monkeypatch.setattr(transfer, "async_get_memory", get_memory)
    for name in (
        "async_get_request_rules",
        "async_get_temporary_memory",
        "async_get_knowledge",
        "async_get_archive",
        "async_get_usage",
        "async_get_guest_mode",
    ):
        monkeypatch.setattr(transfer, name, forbidden)

    document = await transfer.async_collect_transfer_snapshot(
        SimpleNamespace(),
        entry,
        subentry,
        mode="custom",
        sections=[transfer.SECTION_PERSISTENT_MEMORY],
    )
    assert set(document["sections"]) == {transfer.SECTION_PERSISTENT_MEMORY}
    memory.async_backup_data.assert_awaited_once()


def test_new_transfer_normalizes_all_detected_secret_markers() -> None:
    document = {
        "format": transfer.TRANSFER_FORMAT,
        "version": transfer.TRANSFER_VERSION,
        "mode": "setup",
        "created_at": "2026-09-08T20:00:00+00:00",
        "integration_version": "5.0.0",
        "agent": {
            "title": "Jarvis",
            "source_entry_id": "entry",
            "source_subentry_id": "agent",
        },
        "sections": {
            transfer.SECTION_CONFIGURATION: {
                "token": "sk-proj-abcdefghijklmnopqrstuvwxyz0123456789",
                "api_key": "literal-secret",
            },
            transfer.SECTION_REQUEST_RULES: {
                "rules": [],
                "authorization": "Bearer abcdefghijklmnopqrstuvwxyz123456",
            },
        },
    }
    redacted = transfer.redact_transfer_document(document)
    serialized = yaml.safe_dump(redacted)
    assert "literal-secret" not in serialized
    assert "sk-proj-" not in serialized
    assert "Bearer abc" not in serialized
    assert "[redacted]" not in serialized
    assert "__extended_openai_redacted_secret__" in serialized


@pytest.mark.parametrize("placeholder", [REDACTED_SECRET_SENTINEL, "[redacted]"])
def test_secret_placeholder_preserves_destination_value(placeholder) -> None:
    restored, preserved, missing = transfer._restore_section_secrets(
        {"prompt": placeholder}, {"prompt": "destination secret"}
    )
    assert restored == {"prompt": "destination secret"}
    assert preserved == ("prompt",)
    assert missing == ()


def test_missing_secret_placeholder_is_reported_and_not_restored_literally() -> None:
    restored, preserved, missing = transfer._restore_section_secrets(
        {"api_key": REDACTED_SECRET_SENTINEL}, {}
    )
    assert restored == {}
    assert preserved == ()
    assert missing == ("api_key",)


def test_legacy_setup_export_is_still_classified() -> None:
    config = agent_config_defaults()
    prepared = transfer.inspect_transfer(
        {
            "schema": transfer.LEGACY_AGENT_SCHEMA,
            "version": AGENT_CONFIG_EXPORT_VERSION,
            "title": "Legacy Jarvis",
            "config": config,
        },
        "target-agent",
    )
    assert prepared.source_kind == "legacy_setup"
    assert prepared.title == "Legacy Jarvis"
    assert prepared.available_sections == frozenset({transfer.SECTION_CONFIGURATION})


def test_current_full_backup_is_classified_without_inventing_absent_sections() -> None:
    document = _document()
    prepared = transfer.inspect_transfer(document, "target-agent")
    assert prepared.source_kind == "full_backup"
    assert transfer.SECTION_CONFIGURATION in prepared.available_sections
    assert transfer.SECTION_REQUEST_RULES in prepared.available_sections
    assert transfer.SECTION_GUEST_MODE not in prepared.available_sections
    assert transfer.SECTION_PERSISTENT_MEMORY in prepared.available_sections


async def test_selective_restore_replaces_only_selected_section(monkeypatch) -> None:
    current = backup.inspect_backup(_document(), "target-agent")
    imported_memory = deepcopy(current.memories)
    imported_memory[0] = replace(imported_memory[0], content="Imported memory")
    imported = transfer.PreparedTransfer(
        source_kind="custom_backup",
        mode="custom",
        title="Imported title",
        available_sections=frozenset({transfer.SECTION_PERSISTENT_MEMORY}),
        created_at="2026-09-08T20:00:00+00:00",
        integration_version="5.0.0",
        memories=imported_memory,
    )
    monkeypatch.setattr(transfer, "_current_snapshot", AsyncMock(return_value=current))
    entry, subentry = _entry_and_subentry()

    target, preview = await transfer.async_materialize_restore(
        SimpleNamespace(),
        entry,
        subentry,
        imported,
        sections=[transfer.SECTION_PERSISTENT_MEMORY],
    )
    assert target.memories[0].content == "Imported memory"
    assert target.title == current.title
    assert target.config == current.config
    assert target.knowledge == current.knowledge
    assert preview["selected_sections"] == [transfer.SECTION_PERSISTENT_MEMORY]


async def test_request_rule_dependency_uses_combined_target_state(monkeypatch) -> None:
    current = backup.inspect_backup(_document(), "target-agent")
    rules = RequestRules.validate_backup_data(_rules_backup([_function_rule()]))
    imported = transfer.PreparedTransfer(
        source_kind="custom_backup",
        mode="custom",
        title="Rules",
        available_sections=frozenset({transfer.SECTION_REQUEST_RULES}),
        created_at="2026-09-08T20:00:00+00:00",
        integration_version="5.0.0",
        request_rules=rules,
    )
    monkeypatch.setattr(transfer, "_current_snapshot", AsyncMock(return_value=current))
    entry, subentry = _entry_and_subentry()

    with pytest.raises(
        backup.BackupError, match="unavailable Function Tool `remember`"
    ):
        await transfer.async_materialize_restore(
            SimpleNamespace(), entry, subentry, imported
        )

    current.config[CONF_FUNCTION_TOOLS] = yaml.safe_dump(
        [_remember_tool()], sort_keys=False
    )
    target, _preview = await transfer.async_materialize_restore(
        SimpleNamespace(), entry, subentry, imported
    )
    assert target.request_rules["rules"][0]["id"] == "remember-rule"


async def test_configuration_secret_placeholder_uses_current_destination(
    monkeypatch,
) -> None:
    current = backup.inspect_backup(_document(), "target-agent")
    current.config[CONF_PROMPT] = "Bearer sk-destination-secret-value-1234567890"
    raw = deepcopy(current.config)
    raw[CONF_PROMPT] = REDACTED_SECRET_SENTINEL
    imported = transfer.PreparedTransfer(
        source_kind="portable_transfer",
        mode="setup",
        title="Imported title",
        available_sections=frozenset({transfer.SECTION_CONFIGURATION}),
        created_at="2026-09-08T20:00:00+00:00",
        integration_version="5.0.0",
        config=deepcopy(current.config),
        raw_configuration=raw,
        redacted_sensitive_fields=(f"configuration.{CONF_PROMPT}",),
    )
    monkeypatch.setattr(transfer, "_current_snapshot", AsyncMock(return_value=current))
    entry, subentry = _entry_and_subentry()

    target, preview = await transfer.async_materialize_restore(
        SimpleNamespace(), entry, subentry, imported
    )
    assert target.config[CONF_PROMPT] == current.config[CONF_PROMPT]
    assert preview["preserved_sensitive_field_count"] == 1
    assert preview["missing_sensitive_field_count"] == 0


def test_section_selection_rejects_empty_and_unknown_values() -> None:
    with pytest.raises(backup.BackupError, match="Select at least one"):
        transfer.validate_section_selection([])
    with pytest.raises(backup.BackupError, match="Unknown transfer section"):
        transfer.validate_section_selection(["not-a-section"])
