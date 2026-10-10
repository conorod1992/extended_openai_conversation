"""Restore and persistence regressions from audit PR 5."""

from dataclasses import asdict
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses import (
    management_function_quarantine as quarantine,
)
from custom_components.extended_openai_conversation_responses.conversation import (
    _ACTIVE_SCOPE,
    ExtendedOpenAIAgentEntity,
)
from custom_components.extended_openai_conversation_responses.conversation_archive import (
    ArchiveSession,
    ArchiveTurn,
    ConversationArchive,
)
from custom_components.extended_openai_conversation_responses.guest_mode import (
    GuestCapabilityPolicy,
)
from custom_components.extended_openai_conversation_responses.memory import (
    PersistentMemory,
)
from custom_components.extended_openai_conversation_responses.scope import (
    SHARED_HOUSEHOLD_SCOPE_ID,
    shared_scope,
)
from custom_components.extended_openai_conversation_responses.secret_redaction import (
    REDACTED_SECRET_SENTINEL,
    redact_secrets,
    restore_redacted_secrets,
)
from tests.test_conversation_archive import FakeArchiveStorage
from tests.test_memory import FakeStorage, _memory


def raw_memory(content="  useful   fact  "):
    return {
        "memory_id": "one",
        "user_id": "alice",
        "content": content,
        "category": " HOME ",
        "source": "explicit",
        "created_at": "2026-10-10T12:00:00+00:00",
        "updated_at": "2026-10-10T12:00:00+00:00",
        "subject": "  Router  ",
        "key": "router.password",
    }


async def test_restored_memory_uses_cleaned_fields_after_restart():
    storage = FakeStorage()
    memory = await _memory(storage)
    records = PersistentMemory.validate_backup_data(
        {"memories": [raw_memory(" x " + " " * 2_000_000)]}
    )
    await memory.async_replace_backup(records)
    reloaded = await _memory(storage)
    restored = await reloaded.async_list("alice", "home")
    assert len(restored) == 1
    assert restored[0].content == "x"
    assert restored[0].subject == "Router"
    assert restored[0].key == "router.password"


def test_oversized_substantive_memory_still_rejected():
    with pytest.raises(ValueError, match="content must"):
        PersistentMemory.validate_backup_data({"memories": [raw_memory("x" * 1001)]})


@pytest.mark.parametrize(
    "instant", ["20261010T120000+0000", "2026-10-01T00:30:00+02:00"]
)
async def test_compact_and_offset_archive_restore_survives_restart(instant):
    storage = FakeArchiveStorage()
    archive = ConversationArchive(storage, "agent")
    await archive.async_initialize()
    session = ArchiveSession(
        "session",
        None,
        "agent",
        "alice",
        "user",
        "test",
        None,
        instant,
        instant,
        "Title",
        1,
        "retained",
    )
    turn = ArchiveTurn("turn", "session", None, instant, "question", "answer", True)
    sessions, turns = archive.validate_backup_data(
        {"sessions": [asdict(session)], "turns": [asdict(turn)]}, "agent"
    )
    await archive.async_replace_backup(sessions, turns)
    reloaded = ConversationArchive(storage, "agent")
    await reloaded.async_initialize()
    assert len(reloaded._turns["session"]) == 1
    assert all(
        len(partition) == 7 and partition[4] == "-" for partition in storage.partitions
    )


async def test_case_sensitive_unkeyed_facts_are_not_silently_discarded():
    memory = await _memory()
    first = await memory.async_upsert(
        "alice", "Project directory: /config/Studio", "home", "explicit"
    )
    second = await memory.async_upsert(
        "alice", "Project directory: /config/studio", "home", "explicit"
    )
    assert first["status"] == "created"
    assert second["status"] == "needs_resolution"
    assert (await memory.async_list("alice"))[0].content.endswith("Studio")


@pytest.mark.parametrize("operation", ["add", "upsert", "update", "delete"])
async def test_guest_write_only_memory_dispatches_with_write_scope(operation):
    memory = await _memory()
    agent = ExtendedOpenAIAgentEntity.__new__(ExtendedOpenAIAgentEntity)
    agent.subentry = SimpleNamespace(
        data={"memory_enabled": True, "shared_memory_mode": "explicit"}
    )
    agent._memory = memory
    agent._effective_guest_policy = lambda: GuestCapabilityPolicy(
        True, shared_memory_read=False, shared_memory_write=True
    )
    token = _ACTIVE_SCOPE.set(shared_scope(source="test"))
    try:
        existing = await memory.async_add(
            SHARED_HOUSEHOLD_SCOPE_ID, "Existing fact", "home", "explicit"
        )
        args = {
            "content": "New fact",
            "category": "home",
            "source": "explicit",
            "memory_id": existing["memory"]["memory_id"],
            "memory_ids": [existing["memory"]["memory_id"]],
        }
        result = await agent._async_execute_memory_tool(operation, args, None)
        assert result["status"] in {"created", "updated", "deleted"}
        with pytest.raises(RuntimeError, match="disabled for this data scope"):
            await agent._async_execute_memory_tool("list", {}, None)
    finally:
        _ACTIVE_SCOPE.reset(token)


def test_configuration_redaction_preserves_named_counts_and_flags():
    raw = {
        "functions": [
            {
                "function": {
                    "type": "script",
                    "sequence": [
                        {
                            "variables": {
                                "token_count": 42,
                                "secret_name": "recipe",
                                "password_enabled": False,
                                "access_token": "private",
                            }
                        },
                        {"action": "notify.send_message", "data": {"token_count": 42}},
                    ],
                }
            }
        ]
    }
    safe = redact_secrets(raw)
    variables = safe["functions"][0]["function"]["sequence"][0]["variables"]
    assert variables == {
        "token_count": 42,
        "secret_name": "recipe",
        "password_enabled": False,
        "access_token": REDACTED_SECRET_SENTINEL,
    }
    restored = restore_redacted_secrets(safe)
    assert (
        restored["functions"][0]["function"]["sequence"][1]["data"]["token_count"] == 42
    )


def test_group_rename_transfers_hidden_quarantined_members():
    raw = [
        {"id": "old", "functions": ["good", "broken"]},
        {"id": "other", "functions": ["other_broken"]},
    ]
    edited = [{"id": "new", "functions": ["good"]}, {"id": "other", "functions": []}]
    restored = quarantine._restore_quarantined_group_members(
        edited, raw, frozenset({"broken", "other_broken"}), ("old", "new")
    )
    assert restored == [
        {"id": "new", "functions": ["good", "broken"]},
        {"id": "other", "functions": ["other_broken"]},
    ]
    assert edited[0]["functions"] == ["good"]


@pytest.mark.parametrize(
    "key",
    [
        "authorization_header",
        "clientSecretValue",
        "api_key_value",
        "password_value",
        "refreshTokenValue",
    ],
)
def test_nonstandard_credential_fields_remain_redacted(key):
    assert redact_secrets({key: "private-value"}) == {key: REDACTED_SECRET_SENTINEL}


@pytest.mark.parametrize("operation", ["add", "upsert", "update"])
@pytest.mark.parametrize("readable", [[], ["another-person"]])
async def test_write_only_memory_never_returns_stored_fields(operation, readable):
    memory = await _memory()
    agent = ExtendedOpenAIAgentEntity.__new__(ExtendedOpenAIAgentEntity)
    agent.subentry = SimpleNamespace(data={"memory_enabled": True})
    agent._memory = memory
    agent._current_readable_memory_scope_ids = lambda context: readable
    agent._current_write_memory_scope_id = lambda *args, **kwargs: (
        SHARED_HOUSEHOLD_SCOPE_ID
    )
    existing = await memory.async_add(
        SHARED_HOUSEHOLD_SCOPE_ID,
        "The spare house key is beneath the porch",
        "home",
        "explicit",
        subject="spare key",
        key="house.key",
    )
    args = {"category": "home", "source": "explicit", "subject": "spare key"}
    if operation == "update":
        args = {"memory_id": existing["memory"]["memory_id"], "category": "security"}
    else:
        args["content"] = "The spare house key is somewhere"
    result = await agent._async_execute_memory_tool(operation, args, None)
    assert set(result) == {"status"}
    if operation != "update":
        args["content"] = "The spare house key is beneath the porch"
        result = await agent._async_execute_memory_tool(operation, args, None)
        assert set(result) == {"status"}
        args["content"] = "Unrelated newly supplied fact"
        args.pop("subject")
        result = await agent._async_execute_memory_tool(operation, args, None)
        assert result == {"status": "created"}


@pytest.mark.parametrize("container", ["variables", "data", "service_data"])
def test_script_payload_redaction_preserves_ordinary_names(container):
    payload = {
        "token_index": 7,
        "token_type": "Bearer",
        "secret_reference": "recipe",
        "access_token": "private",
        "authorization_header": "Bearer private",
        "clientSecretValue": "private",
        "api_key_value": "private",
        "password_value": "private",
        "refreshTokenValue": "private",
    }
    raw = {
        "functions": [
            {
                "function": {
                    "type": "script",
                    "sequence": [{"parallel": [{"sequence": [{container: payload}]}]}],
                }
            }
        ]
    }
    safe = redact_secrets(raw)["functions"][0]["function"]["sequence"][0]["parallel"][
        0
    ]["sequence"][0][container]
    assert safe["token_index"] == 7
    assert safe["token_type"] == "Bearer"
    assert safe["secret_reference"] == "recipe"
    assert all(
        safe[key] == REDACTED_SECRET_SENTINEL
        for key in payload
        if key not in {"token_index", "token_type", "secret_reference"}
    )


def test_local_rule_payload_redaction_preserves_variables():
    raw = {
        "action_type": "local_action",
        "action": {
            "actions": [{"variables": {"token_index": 7, "password": "private"}}]
        },
    }
    safe = redact_secrets(raw)["action"]["actions"][0]["variables"]
    assert safe == {"token_index": 7, "password": REDACTED_SECRET_SENTINEL}


def test_nonsecret_script_variable_backup_roundtrip(hass):
    import yaml
    from custom_components.extended_openai_conversation_responses import backup
    from tests.test_backup import _document

    config = {
        "functions": [
            {
                "spec": {
                    "name": "ordinal",
                    "parameters": {"type": "object", "properties": {}},
                },
                "function": {
                    "type": "script",
                    "sequence": [
                        {"variables": {"token_index": 7}},
                        {"variables": {"_function_result": "{{ token_index }}"}},
                    ],
                },
            }
        ]
    }
    snapshot = _document()
    snapshot["agent"]["config"] = backup.export_configuration_snapshot(config)
    exported = backup.finalize_backup_snapshot(snapshot)["document"]
    prepared = backup.inspect_backup(exported, "destination")
    function = yaml.safe_load(prepared.config["functions"])[0]["function"]
    assert function["sequence"][0]["variables"]["token_index"] == 7
