"""Configuration acknowledgements are prepared before writes and owner-bound."""

from copy import deepcopy
from unittest.mock import Mock

import pytest
import yaml

from custom_components.extended_openai_conversation_responses import (
    management_function_repair as repair,
    management_loading_performance as loading,
    management_ui as ui,
    services,
)
from tests.test_function_acknowledgement_recovery import (
    function_agent as function_agent,
    send,
)


def repair_candidate(agent):
    _hass, _entry, subentry, *_ = agent
    tool = yaml.safe_load(subentry.data["functions"])[0]
    damaged = deepcopy(tool)
    damaged["spec"]["name"] = "unrelated_legacy_tool"
    damaged["function"]["name"] = "missing_legacy_implementation"
    subentry.data = {
        **subentry.data,
        "functions": yaml.safe_dump([tool, damaged]),
    }
    edited = deepcopy(tool)
    edited["spec"]["description"] = "Saved repair description"
    return edited


def operation(action, tool):
    return {
        "save_one": {"index": 0, "tool": tool},
        "delete_one": {"index": 1},
        "save": {"tools": [tool]},
        "configuration_save": {"config": {"prompt": "Saved repair prompt"}},
    }[action]


def assert_saved(action, subentry):
    tools = yaml.safe_load(subentry.data["functions"])
    assert tools[0]["spec"]["name"] == "native_probe"
    if action in {"save_one", "save"}:
        assert tools[0]["spec"]["description"] == "Saved repair description"
    if action in {"delete_one", "save"}:
        assert len(tools) == 1
    else:
        assert tools[1]["function"]["name"] == "missing_legacy_implementation"
    if action == "configuration_save":
        assert subentry.data["prompt"] == "Saved repair prompt"


async def next_saved_action(agent, acknowledged_revision):
    hass, entry, subentry, *_ = agent
    authoritative = await ui.async_management_command(
        hass,
        "admin",
        True,
        {
            "section": "configuration",
            "action": "get",
            "entry_id": entry.entry_id,
            "subentry_id": subentry.subentry_id,
        },
    )
    assert authoritative["revision"] == acknowledged_revision
    tool = yaml.safe_load(subentry.data["functions"])[0]
    tool["spec"]["description"] = "Following saved action"
    reply = await send(agent, "save", original_name="native_probe", tool=tool)
    reply.send_error.assert_not_called()
    assert (
        yaml.safe_load(subentry.data["functions"])[0]["spec"]["description"]
        == "Following saved action"
    )


@pytest.mark.parametrize(
    "action", ["save_one", "delete_one", "save", "configuration_save"]
)
async def test_repair_projection_failure_precedes_write_and_retry(
    function_agent, monkeypatch, action
):
    hass, _, subentry, *_ = function_agent
    edited = repair_candidate(function_agent)
    before = deepcopy(subentry.data)
    revision = repair.repair_revision(subentry)
    options = ui.agent_config_options
    calls = 0

    def fail_projection():
        nonlocal calls
        calls += 1
        # Configuration save first projects the existing repair for validation.
        # Its later acknowledgement projection is the fault under review.
        if calls == (2 if action == "configuration_save" else 1):
            raise ValueError("projection unavailable")
        return options()

    monkeypatch.setattr(ui, "agent_config_options", fail_projection)
    rejected = await send(
        function_agent, action, section="function_repair", **operation(action, edited)
    )
    assert rejected.send_error.call_args.args[1] == "invalid_request"
    rejected.send_result.assert_not_called()
    assert subentry.data == before
    hass.config_entries.async_update_subentry.assert_not_called()
    monkeypatch.setattr(ui, "agent_config_options", options)
    saved = await send(
        function_agent,
        action,
        section="function_repair",
        revision=revision,
        **operation(action, edited),
    )
    saved.send_error.assert_not_called()
    assert_saved(action, subentry)
    assert hass.config_entries.async_update_subentry.call_count == 1
    assert (
        hass.config_entries.async_update_subentry.call_args.kwargs["data"]
        == subentry.data
    )
    await next_saved_action(
        function_agent, saved.send_result.call_args.args[1]["revision"]
    )
    assert hass.config_entries.async_update_subentry.call_count == 2


@pytest.mark.parametrize(
    "action", ["save_one", "delete_one", "save", "configuration_save"]
)
async def test_optional_repair_metadata_cannot_reject_commit(
    function_agent, monkeypatch, action, caplog
):
    hass, _, subentry, *_ = function_agent
    edited = repair_candidate(function_agent)
    monkeypatch.setattr(
        loading,
        "_agent_snapshot",
        Mock(side_effect=ValueError("PRIVATE-AGENT-METADATA")),
    )
    saved = await send(
        function_agent, action, section="function_repair", **operation(action, edited)
    )
    saved.send_error.assert_not_called()
    acknowledgement = saved.send_result.call_args.args[1]
    assert isinstance(acknowledgement["config"]["functions"], list)
    assert isinstance(acknowledgement["config"]["function_groups"], list)
    assert_saved(action, subentry)
    assert "PRIVATE-AGENT-METADATA" not in caplog.text
    await next_saved_action(function_agent, acknowledgement["revision"])
    assert hass.config_entries.async_update_subentry.call_count == 2


@pytest.mark.parametrize(
    "action", ["save_one", "delete_one", "save", "configuration_save"]
)
async def test_committed_repair_without_revision_requires_authoritative_recovery(
    function_agent, monkeypatch, action, caplog
):
    hass, _, subentry, *_ = function_agent
    edited = repair_candidate(function_agent)
    original_revision = repair.repair_revision(subentry)
    saved_revision = repair.saved_agent_config_revision
    monkeypatch.setattr(
        repair,
        "saved_agent_config_revision",
        Mock(side_effect=ValueError("PRIVATE-REVISION")),
    )
    uncertain = await send(
        function_agent, action, section="function_repair", **operation(action, edited)
    )
    uncertain.send_result.assert_not_called()
    assert uncertain.send_error.call_args.args[1] == "eoai_mutation_uncertain"
    assert "PRIVATE-REVISION" not in uncertain.send_error.call_args.args[2]
    assert "PRIVATE-REVISION" not in caplog.text
    assert_saved(action, subentry)
    assert hass.config_entries.async_update_subentry.call_count == 1
    monkeypatch.setattr(repair, "saved_agent_config_revision", saved_revision)
    stale = await send(
        function_agent,
        "save_one",
        section="function_repair",
        index=0,
        tool=edited,
        revision=original_revision,
    )
    assert stale.send_error.call_args.args[1] == "invalid_request"
    assert hass.config_entries.async_update_subentry.call_count == 1
    await next_saved_action(function_agent, repair.repair_revision(subentry))


async def test_repair_rename_acknowledges_its_committed_baseline_not_a_later_writer(
    function_agent, monkeypatch
):
    hass, entry, subentry, _rules, store = function_agent
    edited = repair_candidate(function_agent)
    edited["spec"]["name"] = "renamed_probe"
    save = store.async_save
    concurrent = None

    async def save_then_toggle(data):
        nonlocal concurrent
        await save(data)
        # A public service toggle does not wait for the Rule writer's lock.
        # It can commit while rename's native Store operation is completing.
        await services.async_set_function_tools_enabled(
            hass, entry.entry_id, subentry.subentry_id, ["renamed_probe"], False
        )
        concurrent = repair.repair_revision(subentry)

    monkeypatch.setattr(
        services,
        "resolve_memory_agent",
        lambda *_: (entry.entry_id, subentry.subentry_id),
    )
    monkeypatch.setattr(hass.config_entries, "async_get_entry", lambda *_: entry)
    monkeypatch.setattr(store, "async_save", save_then_toggle)
    result = await send(
        function_agent, "save_one", section="function_repair", index=0, tool=edited
    )
    result.send_error.assert_not_called()
    response = result.send_result.call_args.args[1]
    assert (
        response["config"]["functions"][0]["spec"]["description"]
        == "Saved repair description"
    )
    assert response["config"]["functions"][0]["enabled"] is True
    assert response["revision"] != concurrent
    assert yaml.safe_load(subentry.data["functions"])[0]["enabled"] is False
    from custom_components.extended_openai_conversation_responses.request_rules import (
        STORAGE_VERSION,
        RequestRules,
        RequestRuleStore,
    )

    fresh = RequestRules(RequestRuleStore(hass, STORAGE_VERSION, store.key))
    await fresh.async_initialize()
    assert fresh.function_references("renamed_probe") and not fresh.function_references(
        "native_probe"
    )
    stale = await send(
        function_agent,
        "save",
        original_name="renamed_probe",
        tool=edited,
        revision=response["revision"],
    )
    assert stale.send_error.call_args.args[1] == "invalid_request"
    assert hass.config_entries.async_update_subentry.call_count == 2
    read = await ui.async_management_command(
        hass,
        "admin",
        True,
        {
            "section": "configuration",
            "action": "get",
            "entry_id": entry.entry_id,
            "subentry_id": subentry.subentry_id,
        },
    )
    assert read["revision"] == concurrent
    following = deepcopy(edited)
    following["spec"]["description"] = "Following saved action"
    following["enabled"] = False
    saved = await send(
        function_agent,
        "save",
        original_name="renamed_probe",
        tool=following,
        revision=read["revision"],
    )
    saved.send_error.assert_not_called()
    assert (
        yaml.safe_load(subentry.data["functions"])[0]["spec"]["description"]
        == "Following saved action"
    )


@pytest.mark.parametrize(
    "groups",
    [None, "legacy groups", [{"id": "unknown", "functions": ["missing_tool"]}]],
)
@pytest.mark.parametrize("representation", ["yaml", "list"])
async def test_repair_settings_projection_preserves_raw_damage_and_omissions(
    function_agent, groups, representation
):
    _hass, _entry, subentry, *_ = function_agent
    repair_candidate(function_agent)
    if representation == "list":
        subentry.data["functions"] = yaml.safe_load(subentry.data["functions"])
    subentry.data = {
        **subentry.data,
        "function_groups": deepcopy(groups),
        "extension_data": {"future": [1, "retained"]},
    }
    subentry.data.pop("reasoning_effort", None)
    raw = deepcopy(subentry.data)
    saved = await send(
        function_agent,
        "configuration_save",
        section="function_repair",
        config={"prompt": "First settings change"},
    )
    saved.send_error.assert_not_called()
    response = saved.send_result.call_args.args[1]
    assert subentry.data["functions"] == raw["functions"]
    assert subentry.data["function_groups"] == groups
    assert subentry.data["extension_data"] == raw["extension_data"]
    assert "reasoning_effort" not in subentry.data
    assert response["function_repair"]["persisted_groups"] == groups
    assert response["config"] == loading._snapshot_normalized_configuration(
        repair.safe_function_configuration(dict(subentry.data))
    )
    following = await send(
        function_agent,
        "configuration_save",
        section="function_repair",
        config={"prompt": "Next settings change"},
        revision=response["revision"],
    )
    following.send_error.assert_not_called()
    assert subentry.data["prompt"] == "Next settings change"
    assert subentry.data["functions"] == raw["functions"]
    assert subentry.data["function_groups"] == groups
    assert "reasoning_effort" not in subentry.data


async def configuration_baseline(agent, legacy):
    """Admit the same authoritative baseline a settings editor receives."""
    if legacy:
        repair_candidate(agent)
    read = await send(agent, "get", section="configuration")
    read.send_error.assert_not_called()
    return read.send_result.call_args.args[1]


async def next_configuration_action(agent, revision):
    hass, _, subentry, *_ = agent
    before = deepcopy(subentry.data)
    result = await send(
        agent,
        "update",
        section="configuration",
        revision=revision,
        config={"prompt": "Following settings action"},
    )
    result.send_error.assert_not_called()
    assert subentry.data["prompt"] == "Following settings action"
    assert subentry.data["functions"] == before["functions"]
    assert subentry.data["function_groups"] == before["function_groups"]
    assert hass.config_entries.async_update_subentry.call_args.kwargs["data"] == (
        subentry.data
    )
    return result.send_result.call_args.args[1]


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("projection", ["snapshot", "capabilities", "local_handling"])
async def test_configuration_update_projection_failure_precedes_write(
    function_agent, monkeypatch, legacy, projection
):
    hass, _, subentry, *_ = function_agent
    baseline = await configuration_baseline(function_agent, legacy)
    before = deepcopy(subentry.data)
    owner, name = {
        "snapshot": (loading, "_snapshot_normalized_configuration"),
        "capabilities": (ui, "model_capabilities"),
        "local_handling": (ui, "local_handling_snapshot"),
    }[projection]
    original = getattr(owner, name)
    monkeypatch.setattr(owner, name, Mock(side_effect=ValueError("projection failed")))
    updates = {
        "prompt": "Saved settings prompt",
        ui.CONF_LOCAL_INTENTS_ENABLED: not baseline["config"][
            ui.CONF_LOCAL_INTENTS_ENABLED
        ],
    }
    reply = await send(
        function_agent,
        "update",
        section="configuration",
        config=updates,
        revision=baseline["revision"],
    )
    assert reply.send_error.call_args.args[1] == "invalid_request"
    reply.send_result.assert_not_called()
    assert subentry.data == before
    hass.config_entries.async_update_subentry.assert_not_called()
    monkeypatch.setattr(owner, name, original)
    saved = await send(
        function_agent,
        "update",
        section="configuration",
        config=updates,
        revision=baseline["revision"],
    )
    saved.send_error.assert_not_called()
    ack = saved.send_result.call_args.args[1]
    assert ack["valid"] is True
    assert ack["config"]["prompt"] == subentry.data["prompt"] == "Saved settings prompt"
    assert subentry.data["functions"] == before["functions"]
    assert subentry.data["function_groups"] == before["function_groups"]
    await next_configuration_action(function_agent, ack["revision"])
    assert hass.config_entries.async_update_subentry.call_count == 2


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("enrichment", ["cache", "decoration"])
async def test_configuration_update_optional_enrichment_preserves_commit(
    function_agent, monkeypatch, legacy, enrichment, caplog
):
    hass, _, subentry, *_ = function_agent
    baseline = await configuration_baseline(function_agent, legacy)
    before = deepcopy(subentry.data)
    # Run through the actual WebSocket dispatcher: successful update must retain
    # explicit commit evidence even if its final guidance decoration fails.
    name = {
        "cache": "seed_persisted_config_projection",
        "decoration": "decorate_configuration_result",
    }[enrichment]
    monkeypatch.setattr(
        ui, name, Mock(side_effect=ValueError("PRIVATE-OPTIONAL-PROJECTION"))
    )
    saved = await send(
        function_agent,
        "update",
        section="configuration",
        revision=baseline["revision"],
        config={"prompt": "Saved settings prompt"},
    )
    saved.send_error.assert_not_called()
    ack = saved.send_result.call_args.args[1]
    assert ack["valid"] is True
    assert ack["config"]["prompt"] == subentry.data["prompt"] == "Saved settings prompt"
    assert ack["revision"] == repair.repair_revision(subentry)
    assert isinstance(ack["config"]["functions"], list)
    assert isinstance(ack["config"]["function_groups"], list)
    assert "PRIVATE-OPTIONAL-PROJECTION" not in caplog.text
    assert subentry.data["functions"] == before["functions"]
    assert subentry.data["function_groups"] == before["function_groups"]
    await next_configuration_action(function_agent, ack["revision"])
    assert hass.config_entries.async_update_subentry.call_count == 2


@pytest.mark.parametrize("legacy", [False, True])
async def test_configuration_update_revision_failure_retains_commit_evidence(
    function_agent, monkeypatch, legacy, caplog
):
    hass, _, subentry, *_ = function_agent
    baseline = await configuration_baseline(function_agent, legacy)
    before = deepcopy(subentry.data)
    original = repair.saved_agent_config_revision
    monkeypatch.setattr(
        repair,
        "saved_agent_config_revision",
        Mock(side_effect=ValueError("PRIVATE-SAVED-REVISION")),
    )
    uncertain = await send(
        function_agent,
        "update",
        section="configuration",
        revision=baseline["revision"],
        config={"prompt": "Saved settings prompt"},
    )
    uncertain.send_result.assert_not_called()
    assert uncertain.send_error.call_args.args[1] == "eoai_mutation_uncertain"
    assert "PRIVATE-SAVED-REVISION" not in uncertain.send_error.call_args.args[2]
    assert "PRIVATE-SAVED-REVISION" not in caplog.text
    assert subentry.data["prompt"] == "Saved settings prompt"
    assert subentry.data["functions"] == before["functions"]
    assert hass.config_entries.async_update_subentry.call_count == 1
    monkeypatch.setattr(repair, "saved_agent_config_revision", original)
    stale = await send(
        function_agent,
        "update",
        section="configuration",
        revision=baseline["revision"],
        config={"prompt": "Unadmitted retry"},
    )
    assert stale.send_error.call_args.args[1] == "invalid_request"
    assert hass.config_entries.async_update_subentry.call_count == 1
    read = await send(function_agent, "get", section="configuration")
    read.send_error.assert_not_called()
    await next_configuration_action(
        function_agent, read.send_result.call_args.args[1]["revision"]
    )
