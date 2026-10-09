"""Credential, recovery and Archive regressions found in PR review."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import (
    backup,
    memory,
    transfer,
)
from custom_components.extended_openai_conversation_responses.agent_config import (
    agent_config_defaults,
)
from custom_components.extended_openai_conversation_responses.conversation_archive import (
    _excerpt,
)
from tests.test_backup import _document
from tests.test_memory_v2 import FakeStorage
from tests.test_transfer_user_mapping import _prepared


@pytest.mark.parametrize(
    "text",
    [
        "wifi_password=synthetic-secret",
        "door_pin: 2468",
        "service_api_key=synthetic-secret",
        "wifiPassword=synthetic-secret",
        "clientSecret=synthetic-secret",
        "myApiKey=synthetic-secret",
        "doorPIN: 2468",
        "authToken=synthetic-secret",
    ],
)
async def test_implicit_memory_rejects_credential_labels(text):
    manager = memory.PersistentMemory(FakeStorage())
    await manager.async_initialize()
    with pytest.raises(ValueError):
        await manager.async_add("alice", text, "general", "implicit")
    assert not (await manager.async_backup_data())["memories"]
    memory.validate_memory_privacy("Chopin is my favourite composer", automatic=True)


@pytest.mark.parametrize(
    "text",
    [
        "Chopin is my favourite composer",
        "CHOPIN is my favourite composer",
        "The lapin is a rabbit",
    ],
)
def test_secret_suffix_inside_ordinary_word_is_not_a_credential(text):
    memory.validate_memory_privacy(text, automatic=True)


async def test_private_rollback_and_selected_restore_preserve_malformed_destination():
    config = agent_config_defaults()
    config["functions"] = "- spec: [unclosed"
    config["function_groups"] = []
    subentry = SimpleNamespace(title="Agent", subentry_id="agent", data=config)
    document = _document()
    document["guest_mode"] = {"schedule": None}
    managers = tuple(
        SimpleNamespace(async_backup_data=AsyncMock(return_value=document[key]))
        for key in (
            "memories",
            "temporary_memories",
            "knowledge",
            "archive",
            "usage",
            "guest_mode",
            "request_rules",
        )
    )
    current = await backup._snapshot_for_restore(managers, subentry)
    assert current.config["functions"] == config["functions"]
    with pytest.raises(backup.BackupError, match="malformed Function YAML"):
        backup.export_configuration_snapshot(config)
    imported = _prepared()
    imported.raw_configuration = backup.export_configuration_snapshot(
        agent_config_defaults()
    )
    selected, _, _ = transfer._prepared_restore_from_selection(
        current, imported, frozenset({transfer.SECTION_CONFIGURATION})
    )
    assert selected.config["functions"] != config["functions"]
    assert current.config["functions"] == config["functions"]


@pytest.mark.parametrize(
    "query,sentence",
    [
        ("batteries", "The batteries are in the kitchen drawer"),
        ("battery", "The batteries are in the kitchen drawer"),
        ("batteries drawer", "The batteries are in the kitchen drawer"),
    ],
)
def test_archive_excerpt_uses_actual_matching_word_offsets(query, sentence):
    assert "batteries" in _excerpt("irrelevant " * 100 + sentence, query)


def test_archive_private_validation_does_not_rescan_sessions_for_every_turn(
    monkeypatch,
):
    from custom_components.extended_openai_conversation_responses import (
        conversation_archive as archive,
    )

    reads = 0

    class Session:
        retention_state = "retained"
        turn_count = 2

        def __init__(self, raw):
            self.key = raw["session_id"]

        @property
        def session_id(self):
            nonlocal reads
            reads += 1
            return self.key

    monkeypatch.setattr(archive, "_validated_session", Session)
    monkeypatch.setattr(archive, "_validated_turn", lambda raw: SimpleNamespace(**raw))
    data = {
        "sessions": [{"session_id": str(i)} for i in range(500)],
        "turns": [
            {"session_id": str(i), "turn_id": f"{i}-{j}"}
            for i in range(500)
            for j in range(2)
        ],
    }
    sessions, turns = archive.ConversationArchive.validate_backup_data(data, "agent")
    assert len(sessions) == 500 and len(turns) == 1000
    assert reads < 500 * 10
