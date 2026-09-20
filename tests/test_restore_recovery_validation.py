"""Restore journal and persisted configuration validation."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses import (
    backup,
    restore_recovery,
)
from tests.test_backup import _document


def _states():
    target = backup.inspect_backup(_document(), "agent-new")
    rollback = deepcopy(target)
    rollback.title = "Before restore"
    rollback.config = {**rollback.config, "prompt": "old configuration"}
    target.title = "Restored"
    target.config = {**target.config, "prompt": "new configuration"}
    return target, rollback


def _journal():
    target, rollback = _states()
    return restore_recovery._new_journal("entry-1", "agent-new", target, rollback)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: ["not", "a", "mapping"],
        lambda value: {**value, "extra": True},
        lambda value: {**value, "transaction_id": ""},
        lambda value: {**value, "entry_id": "wrong-entry"},
        lambda value: {**value, "subentry_id": "wrong-agent"},
        lambda value: {**value, "phase": "unknown"},
        lambda value: {**value, "created_at": "not-a-timestamp"},
    ],
)
def test_load_journal_rejects_corrupt_envelope(mutate) -> None:
    with pytest.raises(
        backup.BackupError, match="Pending restore transaction is corrupted"
    ):
        restore_recovery._load_journal(mutate(_journal()), "entry-1", "agent-new")


def test_load_journal_rejects_invalid_embedded_backup() -> None:
    journal = _journal()
    journal["target"] = {"not": "a valid backup"}

    with pytest.raises(
        backup.BackupError, match="Pending restore transaction is corrupted"
    ):
        restore_recovery._load_journal(journal, "entry-1", "agent-new")


def test_load_journal_returns_validated_target_and_rollback() -> None:
    journal = _journal()

    loaded, target, rollback = restore_recovery._load_journal(
        journal, "entry-1", "agent-new"
    )

    assert loaded == journal
    assert target.title == "Restored"
    assert rollback.title == "Before restore"


def test_persisted_subentry_matcher_rejects_malformed_snapshots() -> None:
    prepared, _rollback = _states()
    valid = {
        "entries": [
            {
                "entry_id": "entry-1",
                "subentries": [
                    {
                        "subentry_id": "agent-new",
                        "title": prepared.title,
                        "data": deepcopy(prepared.config),
                    }
                ],
            }
        ]
    }

    assert (
        restore_recovery._persisted_subentry_matches(
            valid, "entry-1", "agent-new", prepared
        )
        is True
    )
    assert (
        restore_recovery._persisted_subentry_matches(
            None, "entry-1", "agent-new", prepared
        )
        is False
    )
    assert (
        restore_recovery._persisted_subentry_matches(
            {"entries": {}}, "entry-1", "agent-new", prepared
        )
        is False
    )
    assert (
        restore_recovery._persisted_subentry_matches(
            {"entries": [{"entry_id": "entry-1", "subentries": {}}]},
            "entry-1",
            "agent-new",
            prepared,
        )
        is False
    )
    assert (
        restore_recovery._persisted_subentry_matches(
            {"entries": [{"entry_id": "other", "subentries": []}]},
            "entry-1",
            "agent-new",
            prepared,
        )
        is False
    )

    wrong_title = deepcopy(valid)
    wrong_title["entries"][0]["subentries"][0]["title"] = "Wrong"
    assert (
        restore_recovery._persisted_subentry_matches(
            wrong_title, "entry-1", "agent-new", prepared
        )
        is False
    )


def test_persisted_subentry_matcher_stops_after_matching_entry() -> None:
    prepared, _rollback = _states()
    value = {
        "entries": [
            {"entry_id": "entry-1", "subentries": []},
            {
                "entry_id": "entry-1",
                "subentries": [
                    {
                        "subentry_id": "agent-new",
                        "title": prepared.title,
                        "data": prepared.config,
                    }
                ],
            },
        ]
    }

    assert (
        restore_recovery._persisted_subentry_matches(
            value, "entry-1", "agent-new", prepared
        )
        is False
    )


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (None, False),
        ({"entries": {}}, False),
        ({"entries": [None]}, False),
        (
            {
                "entries": [
                    {"entry_id": "entry-1", "subentries": {}},
                ]
            },
            False,
        ),
        (
            {
                "entries": [
                    {
                        "entry_id": "entry-1",
                        "subentries": [
                            {
                                "subentry_id": "other",
                                "title": "Restored",
                                "data": {"x": 1},
                            }
                        ],
                    }
                ]
            },
            False,
        ),
        (
            {
                "entries": [
                    {
                        "entry_id": "entry-1",
                        "subentries": [
                            {
                                "subentry_id": "agent-1",
                                "title": "Wrong",
                                "data": {"x": 1},
                            }
                        ],
                    }
                ]
            },
            False,
        ),
        (
            {
                "entries": [
                    {
                        "entry_id": "entry-1",
                        "subentries": [
                            {
                                "subentry_id": "agent-1",
                                "title": "Restored",
                                "data": {"x": 1},
                            }
                        ],
                    }
                ]
            },
            True,
        ),
    ],
)
def test_persisted_subentry_match_is_strict_about_core_store_shape(
    payload, expected
) -> None:
    """Recovery cleanup requires the exact target subentry in a valid Core snapshot."""
    prepared = SimpleNamespace(title="Restored", config={"x": 1})

    assert (
        restore_recovery._persisted_subentry_matches(
            payload, "entry-1", "agent-1", prepared
        )
        is expected
    )
