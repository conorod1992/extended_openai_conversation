"""Regression coverage for selected validation and ordered secret restoration."""

from copy import deepcopy
from types import SimpleNamespace

import pytest
import yaml

from custom_components.extended_openai_conversation_responses import backup, transfer
from custom_components.extended_openai_conversation_responses.agent_config import (
    AgentConfigError,
)
from custom_components.extended_openai_conversation_responses.management_ui import (
    _export_agent,
    _parse_import_document,
)
from custom_components.extended_openai_conversation_responses.secret_redaction import (
    redact_secrets,
)
from tests.test_backup import _document
from tests.test_secret_restore_context import _rest_tool
from tests.test_transfer import _portable_document


@pytest.mark.parametrize("duplicate", [False, True])
def test_composite_sequence_retains_order_and_supports_duplicates(duplicate):
    first = {
        "type": "template",
        "value_template": "sk-privateaaaaaaaaaaaa",
        "response_variable": "token",
    }
    second = (
        deepcopy(first)
        if duplicate
        else {
            "type": "rest",
            "resource": "https://safe.test",
            "headers": {"Authorization": "private-b"},
        }
    )
    config = {"type": "composite", "sequence": [first, second]}
    raw = redact_secrets(config)
    restored, preserved, missing = transfer._restore_section_secrets(raw, config)
    assert restored == config
    assert preserved and not missing
    if not duplicate:
        raw["sequence"].reverse()
        _, preserved, missing = transfer._restore_section_secrets(raw, config)
        assert missing and not preserved


@pytest.mark.parametrize("full", [False, True])
def test_inspection_and_selected_memory_ignore_invalid_configuration(full):
    document = _document() if full else _portable_document()
    config = (
        document["agent"]["config"] if full else document["sections"]["configuration"]
    )
    config["max_tokens"] = 0
    inspected = transfer.inspect_transfer(document, "target", inspect_only=True)
    assert transfer.SECTION_PERSISTENT_MEMORY in inspected.available_sections
    selected = transfer.inspect_transfer(
        document, "target", sections=[transfer.SECTION_PERSISTENT_MEMORY]
    )
    assert selected.memories
    with pytest.raises(backup.BackupError):
        transfer.inspect_transfer(
            document, "target", sections=[transfer.SECTION_CONFIGURATION]
        )


def test_agent_roundtrip_preserves_destination_headers_and_rejects_new_secrets(hass):
    config = _document()["agent"]["config"]
    config["functions"] = [_rest_tool()]
    config["function_groups"] = []
    agent = SimpleNamespace(title="Agent", data=config)
    exported = _export_agent(agent)
    imported = _parse_import_document(exported, destination=agent)
    assert (
        yaml.safe_load(imported["config"]["functions"])[0]["function"]["headers"][
            "Authorization"
        ]
        == "Bearer local-credential"
    )
    with pytest.raises(AgentConfigError, match="unavailable secrets"):
        _parse_import_document(exported)
    exported["config"]["functions"][0]["function"]["resource"] = "https://attacker.test"
    with pytest.raises(AgentConfigError, match="unavailable secrets"):
        _parse_import_document(exported, destination=agent)


def test_required_template_secrets_are_restored_before_validation(hass):
    config = _document()["agent"]["config"]
    tool = _rest_tool()
    tool["function"] = {"type": "template", "value_template": "sk-privatebbbbbbbbbbbb"}
    config["functions"] = [tool]
    config["function_groups"] = []
    agent = SimpleNamespace(title="Agent", data=config)
    parsed = _parse_import_document(_export_agent(agent), destination=agent)
    assert (
        yaml.safe_load(parsed["config"]["functions"])[0]["function"]["value_template"]
        == "sk-privatebbbbbbbbbbbb"
    )
