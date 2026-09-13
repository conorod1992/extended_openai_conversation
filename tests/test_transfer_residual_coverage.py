"""Residual safety and validation coverage for unified transfers."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import backup, transfer
from custom_components.extended_openai_conversation_responses.agent_config import (
    agent_config_defaults,
)
from custom_components.extended_openai_conversation_responses.const import CONF_PROMPT
from custom_components.extended_openai_conversation_responses.secret_redaction import (
    REDACTED_SECRET_SENTINEL,
)
from tests.test_backup import _document
from tests.test_transfer import _entry_and_subentry, _rules_backup


def _portable_document(*, mode: str = "custom") -> dict:
    source = _document()
    sections = {
        transfer.SECTION_CONFIGURATION: source["agent"]["config"],
        transfer.SECTION_REQUEST_RULES: source["request_rules"],
        transfer.SECTION_PERSISTENT_MEMORY: source["memories"],
        transfer.SECTION_TEMPORARY_MEMORY: source["temporary_memories"],
        transfer.SECTION_KNOWLEDGE: source["knowledge"],
        transfer.SECTION_CONVERSATION_ARCHIVE: source["archive"],
        transfer.SECTION_USAGE: source["usage"],
        transfer.SECTION_GUEST_MODE: {"schedule": None},
    }
    if mode == "setup":
        sections = {
            key: sections[key]
            for key in (transfer.SECTION_CONFIGURATION, transfer.SECTION_REQUEST_RULES)
        }
    return {
        "format": transfer.TRANSFER_FORMAT,
        "version": transfer.TRANSFER_VERSION,
        "mode": mode,
        "created_at": "2026-09-13T12:00:00+00:00",
        "integration_version": "5.0.0",
        "agent": {
            "title": "Coverage agent",
            "source_entry_id": "entry-source",
            "source_subentry_id": "agent-source",
        },
        "sections": sections,
    }


@pytest.mark.parametrize(
    ("sections", "message"),
    [
        ("configuration", "must be a list"),
        (42, "must be a list"),
        ([transfer.SECTION_CONFIGURATION, 42], "contain names only"),
    ],
)
def test_section_selection_rejects_ambiguous_types(sections, message) -> None:
    with pytest.raises(backup.BackupError, match=message):
        transfer.validate_section_selection(sections)


async def test_custom_snapshot_collects_every_selected_manager(monkeypatch) -> None:
    entry, subentry = _entry_and_subentry()
    monkeypatch.setattr(transfer, "agent_config_snapshot", lambda value: dict(value))
    payloads = {
        "async_get_request_rules": _rules_backup(),
        "async_get_memory": {"memories": []},
        "async_get_temporary_memory": {"records": []},
        "async_get_knowledge": {"sources": []},
        "async_get_archive": {"sessions": [], "turns": []},
        "async_get_usage": {
            "totals": {},
            "daily": {},
            "requests": [],
            "runs": [],
        },
        "async_get_guest_mode": {"schedule": None},
    }
    managers = {}
    for getter_name, payload in payloads.items():
        manager = SimpleNamespace(async_backup_data=AsyncMock(return_value=payload))
        managers[getter_name] = manager

        async def getter(_hass, _entry_id, _subentry_id, *, _manager=manager):
            return _manager

        monkeypatch.setattr(transfer, getter_name, getter)

    result = await transfer.async_collect_transfer_snapshot(
        SimpleNamespace(),
        entry,
        subentry,
        mode="custom",
        sections=transfer.ALL_SECTIONS,
    )

    assert set(result["sections"]) == transfer.ALL_SECTIONS
    assert result["sections"][transfer.SECTION_CONFIGURATION]
    for manager in managers.values():
        manager.async_backup_data.assert_awaited_once()


async def test_snapshot_rejects_unknown_mode() -> None:
    entry, subentry = _entry_and_subentry()
    with pytest.raises(backup.BackupError, match="mode must be setup or custom"):
        await transfer.async_collect_transfer_snapshot(
            SimpleNamespace(), entry, subentry, mode="full"
        )


def test_setup_export_enforces_serialized_size_limit(monkeypatch) -> None:
    monkeypatch.setattr(backup, "MAX_LEGACY_EXPORT_BYTES", 1)
    with pytest.raises(backup.BackupError, match="16 MB safety limit"):
        transfer.finalize_setup_export(_portable_document(mode="setup"))


def test_secret_restore_matches_reordered_items_by_identity() -> None:
    imported = [
        {"id": "first", "token": REDACTED_SECRET_SENTINEL},
        {"id": "second", "token": REDACTED_SECRET_SENTINEL},
    ]
    destination = [
        {"id": "second", "token": "second-secret"},
        {"id": "first", "token": "first-secret"},
    ]

    restored, preserved, missing = transfer._restore_section_secrets(
        imported, destination
    )

    assert restored == [
        {"id": "first", "token": "first-secret"},
        {"id": "second", "token": "second-secret"},
    ]
    assert preserved == ("[0].token", "[1].token")
    assert missing == ()


def test_secret_restore_rejects_incompatible_or_duplicate_list_context() -> None:
    imported = [{"id": "duplicate", "token": REDACTED_SECRET_SENTINEL}]
    destination = [
        {"id": "duplicate", "token": "one"},
        {"id": "duplicate", "token": "two"},
    ]

    restored, preserved, missing = transfer._restore_section_secrets(
        imported, destination
    )

    assert restored == [{"id": "duplicate"}]
    assert preserved == ()
    assert missing == ("[0].token",)
    assert transfer._collect_secret_paths(REDACTED_SECRET_SENTINEL) == ["value"]
    assert not transfer._compatible_secret_context([], {})


def test_secret_restore_drops_unmatched_list_marker() -> None:
    restored, preserved, missing = transfer._restore_section_secrets(
        [REDACTED_SECRET_SENTINEL], []
    )

    assert restored == []
    assert preserved == ()
    assert missing == ("[0]",)


def test_function_tools_match_secrets_by_stable_spec_name() -> None:
    imported = [{"spec": {"name": "weather"}, "token": REDACTED_SECRET_SENTINEL}]
    destination = [{"spec": {"name": "weather"}, "token": "destination"}]

    restored, preserved, missing = transfer._restore_section_secrets(
        imported, destination
    )

    assert restored[0]["token"] == "destination"
    assert preserved == ("[0].token",)
    assert missing == ()
    assert transfer._fallback_list_index({"id": ""}, [{"id": ""}]) is None


def test_custom_transfer_validates_every_supported_section() -> None:
    prepared = transfer.inspect_transfer(_portable_document(), "target-agent")

    assert prepared.available_sections == transfer.ALL_SECTIONS
    assert prepared.memories and prepared.memories[0].memory_id == "memory-1"
    assert prepared.temporary_memories
    assert prepared.knowledge and prepared.knowledge[0].source_id == "source-1"
    assert prepared.archive_sessions == []
    assert prepared.usage_requests[0].agent_subentry_id == "target-agent"
    assert prepared.guest_mode_schedule is None


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value.pop("created_at"), "incomplete or corrupted"),
        (lambda value: value.update(created_at="not-a-date"), "metadata is invalid"),
        (lambda value: value.update(integration_version=""), "metadata is invalid"),
        (lambda value: value.update(agent=[]), "agent metadata is invalid"),
        (
            lambda value: value["agent"].update(extra="field"),
            "agent metadata is invalid",
        ),
        (lambda value: value["agent"].update(title=""), "agent name is invalid"),
        (
            lambda value: value["agent"].update(source_entry_id=3),
            "agent identity is invalid",
        ),
        (lambda value: value.update(version=0), "unsupported format version"),
        (lambda value: value.update(mode="full"), "transfer mode is invalid"),
        (lambda value: value.update(sections=[]), "transfer sections are invalid"),
        (
            lambda value: value["sections"].pop(transfer.SECTION_REQUEST_RULES),
            "setup sections are incomplete",
        ),
        (
            lambda value: value["sections"].update(
                {transfer.SECTION_CONFIGURATION: []}
            ),
            "configuration must be an object",
        ),
    ],
)
def test_portable_transfer_rejects_corrupted_boundaries(mutation, message) -> None:
    document = _portable_document(mode="setup")
    mutation(document)
    with pytest.raises(backup.BackupError, match=message):
        transfer.inspect_transfer(document, "target-agent")


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("{", "not valid JSON"),
        ([], "not an Extended OpenAI Conversation transfer"),
        ({"unrelated": True}, "not a recognised"),
    ],
)
def test_inspection_rejects_unrecognised_inputs(value, message) -> None:
    with pytest.raises(backup.BackupError, match=message):
        transfer.inspect_transfer(value, "target-agent")


def test_inspection_enforces_raw_json_size_limit(monkeypatch) -> None:
    monkeypatch.setattr(backup, "MAX_BACKUP_BYTES", 1)
    with pytest.raises(backup.BackupError, match="128 MB safety limit"):
        transfer.inspect_transfer(json.dumps({}), "target-agent")


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value.update(version=0), "unsupported version"),
        (lambda value: value.update(extra=True), "unknown fields"),
        (lambda value: value.update(config=[]), "setup export is invalid"),
    ],
)
def test_legacy_setup_rejects_corrupted_boundaries(mutation, message) -> None:
    document = {
        "schema": transfer.LEGACY_AGENT_SCHEMA,
        "version": transfer.AGENT_CONFIG_EXPORT_VERSION,
        "title": "Legacy",
        "config": agent_config_defaults(),
    }
    mutation(document)
    with pytest.raises(backup.BackupError, match=message):
        transfer.inspect_transfer(document, "target-agent")


def test_full_backup_classifies_optional_guest_mode() -> None:
    document = _document()
    document["guest_mode"] = {"schedule": None}

    prepared = transfer.inspect_transfer(document, "target-agent")

    assert transfer.SECTION_GUEST_MODE in prepared.available_sections


def test_older_full_backup_does_not_invent_newer_sections() -> None:
    document = _document()
    document["version"] = 2
    document.pop("request_rules")

    prepared = transfer.inspect_transfer(document, "target-agent")

    assert transfer.SECTION_REQUEST_RULES not in prepared.available_sections
    assert prepared.request_rules is None


@pytest.mark.parametrize("section", ["configuration", "request_rules"])
def test_selected_setup_section_must_have_materialized_payload(section) -> None:
    current = backup.inspect_backup(_document(), "target-agent")
    imported = transfer.PreparedTransfer(
        source_kind="portable_transfer",
        mode="setup",
        title="Imported",
        available_sections=frozenset({section}),
        created_at=None,
        integration_version=None,
    )

    with pytest.raises(backup.BackupError, match="unavailable"):
        transfer._prepared_restore_from_selection(
            current, imported, frozenset({section})
        )


def test_selected_raw_configuration_must_restore_to_mapping() -> None:
    current = backup.inspect_backup(_document(), "target-agent")
    imported = transfer.PreparedTransfer(
        source_kind="portable_transfer",
        mode="setup",
        title="Imported",
        available_sections=frozenset({transfer.SECTION_CONFIGURATION}),
        created_at=None,
        integration_version=None,
        raw_configuration=[],
    )

    with pytest.raises(backup.BackupError, match="configuration is invalid"):
        transfer._prepared_restore_from_selection(
            current, imported, frozenset({transfer.SECTION_CONFIGURATION})
        )


async def test_dependency_validation_skips_non_rule_records(monkeypatch) -> None:
    validate = AsyncMock()
    monkeypatch.setattr(transfer, "async_validate_request_rule_functions", validate)
    monkeypatch.setattr(
        transfer, "configured_function_tools_from_data", lambda _data: []
    )
    rule = {"id": "valid-shape"}

    await transfer._async_validate_request_rule_function_dependencies(
        SimpleNamespace(), {"rules": [None, rule]}, agent_config_defaults()
    )

    validate.assert_awaited_once()
    assert validate.await_args.args[1] is rule


def test_frontend_inspection_reports_create_agent_eligibility() -> None:
    prepared = transfer.inspect_transfer(_portable_document(mode="setup"), "target")

    result = transfer.inspection_for_frontend(prepared)

    assert result["valid"] is True
    assert result["can_create_new_agent"] is True
    assert result["available_sections"] == list(transfer.SECTION_ORDER[:2])


def test_literal_text_context_requires_exact_destination_value() -> None:
    literal = {transfer.LITERAL_TEXT_KEY: "keep this literal"}

    assert transfer._compatible_secret_context(literal, "keep this literal")
    assert not transfer._compatible_secret_context(literal, "different")
    restored, preserved, missing = transfer._restore_section_secrets(
        {CONF_PROMPT: literal}, {CONF_PROMPT: "different"}
    )
    assert restored == {CONF_PROMPT: "keep this literal"}
    assert preserved == missing == ()
    assert transfer._normalize_redaction_placeholders(literal) == literal
    assert transfer._collect_secret_paths(literal) == []


def test_redaction_handles_partial_or_non_mapping_sections() -> None:
    document = _portable_document(mode="setup")
    document["sections"] = "not-a-mapping"
    assert transfer.redact_transfer_document(document)["sections"] == "not-a-mapping"

    document["sections"] = {transfer.SECTION_CONFIGURATION: {"api_key": "ordinary"}}
    redacted = transfer.redact_transfer_document(document)["sections"]
    assert redacted[transfer.SECTION_CONFIGURATION]["api_key"] == dict(
        REDACTED_SECRET_SENTINEL
    )

    document["sections"] = {transfer.SECTION_REQUEST_RULES: {"rules": []}}
    assert (
        transfer.redact_transfer_document(document)["sections"] == document["sections"]
    )
