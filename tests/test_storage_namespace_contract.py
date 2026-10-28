"""Literal on-disk identities and exact cleanup ownership remain compatible."""

from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses import (
    agent_deletion,
    storage_namespaces,
)

SECTIONS = (
    "memory",
    "temporary_memory",
    "knowledge",
    "archive",
    "usage",
    "guest_mode",
    "request_rules",
    "restore_transaction",
)


def witness(tmp_path):
    directory = tmp_path / ".storage"
    directory.mkdir(exist_ok=True)
    expected = {
        f"extended_openai_conversation_responses.{section}.entry.agent"
        for section in SECTIONS
    }
    expected |= {
        "extended_openai_conversation_responses.archive.entry.agent.turns.2026",
        "extended_openai_conversation_responses.memory.entry.agent.embeddings",
    }
    unrelated = {
        "extended_openai_conversation_responses.memory.entry.agent-other",
        "extended_openai_conversation_responses.memory.entry-other.agent",
        "extended_openai_conversation_responses.broadcast",
        "extended_openai_conversation_responses.quiet_hours",
    }
    for name in expected | unrelated:
        (directory / name).touch()
    hass = SimpleNamespace(
        config=SimpleNamespace(path=lambda name: str(tmp_path / name))
    )
    assert set(agent_deletion._storage_names(hass, "entry", "agent")) == expected
    assert agent_deletion._stored_provider_ids(hass) == {"entry", "entry-other"}


def test_literal_storage_identity_and_cleanup(tmp_path):
    assert (
        tuple(
            f"extended_openai_conversation_responses.{section}" for section in SECTIONS
        )
        == storage_namespaces.AGENT_STORAGE_PREFIXES
    )
    witness(tmp_path)


@pytest.mark.parametrize("missing", ["archive", "restore_transaction"])
def test_cleanup_contract_detects_missing_namespace(tmp_path, monkeypatch, missing):
    monkeypatch.setattr(
        agent_deletion,
        "AGENT_STORAGE_PREFIXES",
        tuple(
            prefix
            for prefix in storage_namespaces.AGENT_STORAGE_PREFIXES
            if not prefix.endswith("." + missing)
        ),
    )
    with pytest.raises(AssertionError):
        witness(tmp_path)


@pytest.mark.parametrize(
    ("module", "attribute", "section"),
    [
        ("memory", "STORAGE_KEY_PREFIX", "memory"),
        ("temporary_memory", "STORAGE_KEY_PREFIX", "temporary_memory"),
        ("knowledge", "STORAGE_KEY_PREFIX", "knowledge"),
        ("conversation_archive", "STORAGE_KEY_PREFIX", "archive"),
        ("usage", "STORAGE_KEY_PREFIX", "usage"),
        ("guest_mode", "GUEST_MODE_STORAGE_PREFIX", "guest_mode"),
        ("request_rules", "STORAGE_KEY_PREFIX", "request_rules"),
        ("restore_recovery", "RESTORE_JOURNAL_PREFIX", "restore_transaction"),
    ],
)
def test_store_construction_aliases_preserve_literal_identity(
    module, attribute, section
):
    from importlib import import_module

    production = import_module(
        "custom_components.extended_openai_conversation_responses." + module
    )
    assert (
        getattr(production, attribute)
        == "extended_openai_conversation_responses." + section
    )
