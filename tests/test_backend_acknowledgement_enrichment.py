from unittest.mock import AsyncMock
import pytest
from custom_components.extended_openai_conversation_responses import (
    management_ui,
    feature_status,
    knowledge,
)
from tests.test_management_ui import _entry_pair, _hass, _message, retained_rules  # noqa: F401
from tests.test_knowledge import FakeStorage


@pytest.mark.parametrize("fault", ["feature", "summary", "stats"])
async def test_committed_knowledge_identity_survives_enrichment_and_next_update(
    monkeypatch, fault
):
    entry, subentry = _entry_pair()
    subentry.data["functions"] = "[]"
    hass = _hass(entry)
    storage = FakeStorage()
    library = knowledge.KnowledgeLibrary(storage)
    await library.async_initialize()
    monkeypatch.setattr(
        management_ui, "async_get_knowledge", AsyncMock(return_value=library)
    )

    def fail(*args, **kwargs):
        raise ValueError("injected optional projection failure")

    if fault == "feature":
        monkeypatch.setattr(feature_status, "management_feature_status", fail)
    elif fault == "summary":
        monkeypatch.setattr(knowledge, "source_summary", fail)
    else:
        monkeypatch.setattr(library, "stats", fail)
    saved = await management_ui.async_management_command(
        hass,
        "admin",
        True,
        _message("knowledge", "create", title="Original", content="Body"),
    )
    source_id = saved["source"]["source_id"]
    fresh = knowledge.KnowledgeLibrary(storage)
    await fresh.async_initialize()
    assert (await fresh.async_get(source_id)).title == "Original"
    updated = await management_ui.async_management_command(
        hass,
        "admin",
        True,
        _message("knowledge", "update", source_id=source_id, title="Next action"),
    )
    assert updated["source"]["source_id"] == source_id
    fresh = knowledge.KnowledgeLibrary(storage)
    await fresh.async_initialize()
    assert fresh.total_source_count == 1
    assert (await fresh.async_get(source_id)).title == "Next action"


@pytest.mark.parametrize(
    "fault", ["snapshot", "capabilities", "agent", "cache", "guidance"]
)
async def test_configuration_projection_failure_has_correct_write_outcome(
    monkeypatch, fault
):
    from copy import deepcopy
    from custom_components.extended_openai_conversation_responses import (
        management_loading_performance as loading,
    )
    from custom_components.extended_openai_conversation_responses.management_function_repair import (
        repair_revision,
    )

    entry, subentry = _entry_pair()
    subentry.data["functions"] = "[]"
    hass = _hass(entry)
    writes = []

    def write(hass, entry, subentry, *, data, **kwargs):
        writes.append(deepcopy(data))
        subentry.data = deepcopy(data)
        if "title" in kwargs:
            subentry.title = kwargs["title"]
        return True

    monkeypatch.setattr(management_ui, "update_configuration_subentry", write)

    def fail(*args, **kwargs):
        raise ValueError("projection witness")

    if fault == "snapshot":
        monkeypatch.setattr(loading, "_snapshot_normalized_configuration", fail)
    elif fault == "capabilities":
        monkeypatch.setattr(management_ui, "_configuration_model_capabilities", fail)
    elif fault == "agent":
        monkeypatch.setattr(loading, "_agent_snapshot", fail)
    elif fault == "guidance":
        monkeypatch.setattr(management_ui, "decorate_configuration_result", fail)
    else:
        monkeypatch.setattr(management_ui, "seed_persisted_config_projection", fail)
    message = _message(
        "configuration",
        "save",
        config={"prompt": "New prompt"},
        revision=repair_revision(subentry),
    )
    if fault in {"snapshot", "capabilities", "agent"}:
        with pytest.raises(ValueError, match="projection witness"):
            await management_ui.async_management_command(hass, "admin", True, message)
        assert writes == []
    else:
        result = await management_ui.async_management_command(
            hass, "admin", True, message
        )
        assert result["valid"] is True
        assert result["revision"] == repair_revision(subentry)
        assert subentry.data["prompt"] == "New prompt"
        message.update(config={"prompt": "Next prompt"}, revision=result["revision"])
        await management_ui.async_management_command(hass, "admin", True, message)
        assert subentry.data["prompt"] == "Next prompt"
        assert len(writes) == 2


async def test_memory_creation_acknowledges_its_committed_record_without_rereading(
    monkeypatch,
):
    from custom_components.extended_openai_conversation_responses.memory import (
        PersistentMemory,
    )
    from tests.test_memory import FakeStorage as MemoryStorage

    entry, subentry = _entry_pair()
    hass = _hass(entry)
    storage = MemoryStorage()
    memory = PersistentMemory(storage)
    await memory.async_initialize()
    monkeypatch.setattr(
        management_ui, "async_get_memory", AsyncMock(return_value=memory)
    )
    monkeypatch.setattr(
        memory,
        "async_get_many",
        AsyncMock(side_effect=OSError("post-write read failed")),
    )
    saved = await management_ui.async_management_command(
        hass,
        "admin",
        True,
        _message(
            "memories", "add", scope_id="shared:household", content="Remember Celsius."
        ),
    )
    memory_id = saved["memory"]["memory_id"]
    assert saved["memory"]["revision"]
    await management_ui.async_management_command(
        hass,
        "admin",
        True,
        _message(
            "memories",
            "update",
            scope_id="shared:household",
            memory_id=memory_id,
            content="Remember Fahrenheit.",
            expected_revision=saved["memory"]["revision"],
        ),
    )
    memory.async_get_many.assert_not_awaited()
    fresh = PersistentMemory(storage)
    await fresh.async_initialize()
    records = await fresh.async_get_many(
        [("shared:household", memory_id)], ["shared:household"]
    )
    assert len(records) == 1
    assert records[0].content == "Remember Fahrenheit."


@pytest.mark.parametrize("fault", ["snapshot", "cache"])
async def test_function_replacement_acknowledgement_prepares_before_write(
    monkeypatch, fault
):
    from copy import deepcopy
    from custom_components.extended_openai_conversation_responses import (
        management_function_repair as repair,
    )
    from custom_components.extended_openai_conversation_responses import (
        management_loading_performance as loading,
    )
    from tests.test_function_groups_mutation import _group

    entry, subentry = _entry_pair()
    subentry.data.update(functions="[]", function_groups=[])
    hass = _hass(entry)
    writes = []

    def write(hass, entry, subentry, *, data):
        writes.append(deepcopy(data))
        subentry.data = deepcopy(data)

    monkeypatch.setattr(repair, "update_live_subentry", write)

    def fail(*args, **kwargs):
        raise ValueError("Function projection witness")

    if fault == "snapshot":
        monkeypatch.setattr(loading, "_snapshot_normalized_configuration", fail)
    else:
        monkeypatch.setattr(repair, "seed_persisted_config_projection", fail)
    group = _group("g", [], "always")
    message = _message(
        "tools", "save_group", group=group, revision=repair.repair_revision(subentry)
    )
    if fault == "snapshot":
        with pytest.raises(ValueError, match="Function projection witness"):
            await management_ui.async_management_command(hass, "admin", True, message)
        assert not writes
        assert not subentry.data["function_groups"]
    else:
        result = await management_ui.async_management_command(
            hass, "admin", True, message
        )
        assert result["functions"] == []
        assert result["function_groups"][0]["id"] == "g"
        assert result["revision"] == repair.repair_revision(subentry)
        message.update(
            group={**result["function_groups"][0], "enabled": False},
            original_id="g",
            revision=result["revision"],
        )
        await management_ui.async_management_command(hass, "admin", True, message)
        assert subentry.data["function_groups"][0]["enabled"] is False
        assert len(writes) == 2


@pytest.mark.parametrize("fault", ["snapshot", "cache"])
@pytest.mark.parametrize("error_type", [ValueError, RuntimeError])
async def test_guest_policy_response_preparation_preserves_write_outcome(
    monkeypatch, fault, error_type
):
    from copy import deepcopy
    from inspect import unwrap
    from types import SimpleNamespace
    from unittest.mock import Mock

    entry, subentry = _entry_pair()
    subentry.data.update(functions="[]", guest_mode_enabled=False)
    hass = _hass(entry)
    writes = []

    def persist(_entry, saved_subentry, **changes):
        writes.append(deepcopy(changes["data"]))
        saved_subentry.data = deepcopy(changes["data"])
        return True

    # Exercise the production live-update owner and WebSocket error conversion;
    # this API double is the independent saved config-entry state.
    hass.config_entries.async_update_subentry.side_effect = persist
    monkeypatch.setattr(
        management_ui, "async_get_guest_mode", AsyncMock(return_value=object())
    )

    def fail(*args, **kwargs):
        raise error_type("Guest policy projection witness")

    target = (
        "guest_policy_editor_snapshot"
        if fault == "snapshot"
        else "seed_persisted_config_projection"
    )
    original = getattr(management_ui, target)
    monkeypatch.setattr(management_ui, target, fail)
    revision = management_ui._agent_config_revision(subentry.data, subentry.title)
    handler = unwrap(management_ui.websocket_management)

    async def send(config, expected_revision):
        connection = SimpleNamespace(
            user=SimpleNamespace(id="admin", is_admin=True),
            send_error=Mock(),
            send_result=Mock(),
        )
        await handler(
            hass,
            connection,
            {
                "id": 1,
                **_message(
                    "guest_mode",
                    "save_policy",
                    config=config,
                    revision=expected_revision,
                ),
            },
        )
        return connection

    first = await send({"guest_mode_enabled": True}, revision)
    if fault == "snapshot":
        first.send_error.assert_called_once_with(
            1, "invalid_request", "Guest policy projection witness"
        )
        first.send_result.assert_not_called()
        assert writes == []
        assert subentry.data["guest_mode_enabled"] is False
        assert (
            management_ui.persisted_config_projection(subentry).revision == revision
        )
        # A rejected preparation neither writes nor consumes the revision. The
        # next deliberate request can still save that originally submitted edit.
        monkeypatch.setattr(management_ui, target, original)
        next_reply = await send({"guest_mode_enabled": True}, revision)
        expected_enabled, expected_writes = True, 1
    else:
        first.send_error.assert_not_called()
        saved = first.send_result.call_args.args[1]
        assert saved["config"]["guest_mode_enabled"] is True
        assert writes[0]["guest_mode_enabled"] is True
        assert subentry.data["guest_mode_enabled"] is True
        assert saved["revision"] == management_ui.persisted_config_projection(subentry).revision
        assert saved["revision"] != revision
        stale = await send({"guest_mode_enabled": False}, revision)
        assert stale.send_error.call_args.args[1] == "invalid_request"
        assert "changed in another tab" in stale.send_error.call_args.args[2]
        stale.send_result.assert_not_called()
        assert len(writes) == 1
        assert subentry.data["guest_mode_enabled"] is True
        # The first usable ACK owns the next request's guard even while the
        # optional cache continues failing on both writes.
        next_reply = await send({"guest_mode_enabled": False}, saved["revision"])
        expected_enabled, expected_writes = False, 2

    next_reply.send_error.assert_not_called()
    result = next_reply.send_result.call_args.args[1]
    assert len(writes) == expected_writes
    assert writes[-1]["guest_mode_enabled"] is expected_enabled
    assert subentry.data["guest_mode_enabled"] is expected_enabled
    assert result["config"]["guest_mode_enabled"] is expected_enabled
    assert dict(subentry.data) == writes[-1]
    assert result["revision"] == management_ui.persisted_config_projection(subentry).revision
