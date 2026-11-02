"""Accepted configuration must reach the actual initial provider/tool boundary."""

from copy import deepcopy
from datetime import UTC, date, datetime
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml

from custom_components.extended_openai_conversation_responses import (
    agent_config,
    management_ui as ui,
    request,
    services,
)
from custom_components.extended_openai_conversation_responses.assistant_response import (
    final_assistant_text,
)
from custom_components.extended_openai_conversation_responses.conversation import (
    ExtendedOpenAIAgentEntity,
)
from custom_components.extended_openai_conversation_responses.conversation_lifecycle import (
    begin_conversation_lifecycle,
    end_conversation_lifecycle,
)
from custom_components.extended_openai_conversation_responses.function_configuration_repair import (
    function_configuration_repair_state,
)
from custom_components.extended_openai_conversation_responses.function_groups import (
    function_tool_runtime_scope,
)
from custom_components.extended_openai_conversation_responses.function_tool_quarantine import (
    _runtime_configured_function_tools,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv, llm
from tests.test_responses_api import _control_stream
from tests.test_tool_exchange_protocol import FakeStream, _chat_log, _final_stream


def _tool(name="marker"):
    return {
        "spec": {"name": name, "parameters": {"type": "object", "properties": {}}},
        "function": {"type": "template", "value_template": "Done"},
    }


def _options(count=1, **updates):
    return agent_config.normalize_agent_config(
        {
            "chat_model": "gpt-4o",
            "api_mode": "chat_completions",
            "functions": [_tool(f"marker_{i}") for i in range(count)],
            "function_groups": [],
            "memory_mode": "off",
            "temporary_memory": "off",
            "knowledge_enabled": False,
            "archive_enabled": False,
            "guest_mode_enabled": False,
            **updates,
        }
    )


def _agent(hass, entry, subentry):
    agent = object.__new__(ExtendedOpenAIAgentEntity)
    agent.hass, agent.entry, agent.subentry = hass, entry, subentry
    agent.entity_id = "conversation.readiness"
    agent._guest_mode = agent.skill_manager = agent._function_groups_runtime = None
    agent._temporary_memory = agent._knowledge = agent._memory = agent._archive = None
    if subentry.data.get("archive_enabled"):
        agent._archive = (
            object()
        )  # Healthy manager; this probe requests no archive operation.
    agent._usage = None
    return agent


@pytest.fixture
def rules(monkeypatch):
    manager = SimpleNamespace(
        revision=lambda: "rules",
        snapshot=lambda: {"rules": []},
        async_backup_data=AsyncMock(return_value={"rules": []}),
    )
    monkeypatch.setattr(ui, "async_get_request_rules", AsyncMock(return_value=manager))
    monkeypatch.setattr(
        ui,
        "async_get_guest_mode",
        AsyncMock(return_value=SimpleNamespace(status=lambda: {})),
    )
    return manager


async def _save(hass, entry, subentry, updates):
    revision = ui.persisted_config_projection(subentry).revision
    return await ui._async_save_configuration(
        ui._ManagementRequest(
            hass,
            "admin",
            True,
            {"config": updates, "revision": revision},
            entry.entry_id,
            subentry.subentry_id,
            entry,
            subentry,
        )
    )


@pytest.mark.parametrize(
    "keyword", ["enum", "default", "examples", "const", "vendor_annotation"]
)
async def test_schema_dates_rejected_at_editor_boundary(
    hass, management_agent, management_message, rules, keyword
):
    entry, subentry = management_agent
    subentry.data = _options(0)
    tool = _tool()
    value = (
        [date(2026, 11, 1)] if keyword in {"enum", "examples"} else date(2026, 11, 1)
    )
    tool["spec"]["parameters"]["properties"]["day"] = {"type": "string", keyword: value}
    original = deepcopy(subentry.data)
    # Both validation and the editor's actual Save must reject before persistence.
    for action in ("validate_yaml", "save"):
        validation = await ui.async_management_command(
            hass,
            "admin",
            True,
            management_message(
                "tools",
                action,
                yaml=yaml.safe_dump(tool),
                revision=ui.persisted_config_projection(subentry).revision,
            ),
        )
        assert validation["valid"] is False
        assert f"spec.parameters.properties.day.{keyword}" in str(validation["errors"])
    assert subentry.data == original
    tool["spec"]["parameters"]["properties"]["day"][keyword] = (
        ["2026-11-01"] if keyword in {"enum", "examples"} else "2026-11-01"
    )
    await ui.async_management_command(
        hass,
        "admin",
        True,
        management_message(
            "tools",
            "save",
            yaml=yaml.safe_dump(tool),
            revision=ui.persisted_config_projection(subentry).revision,
        ),
    )
    assert "marker" in {
        item["spec"]["name"]
        for item in _agent(hass, entry, subentry)._get_function_tools()
    }


@pytest.mark.parametrize(
    "value",
    [
        datetime(2026, 11, 1, tzinfo=UTC),
        b"binary",
        {"a", "b"},
        float("nan"),
        float("inf"),
        {1: "nonstring key"},
        "\ud800",
        {"\ud800": "invalid key"},
    ],
)
def test_all_spec_annotations_require_json_values(hass, value):
    tool = _tool()
    tool["spec"]["parameters"]["examples"] = [{"nested": value}]
    with pytest.raises(
        HomeAssistantError, match=r"spec.parameters.examples\[0\].nested"
    ):
        agent_config.validate_function_tools([tool])


def test_aliases_allowed_but_recursive_spec_is_rejected(hass):
    tool = _tool()
    shared = {"literal": [False, 0, None, "é"]}
    tool["spec"]["parameters"].update(default=shared, examples=[shared])
    agent_config.validate_function_tools([tool])
    shared["recursive"] = shared
    with pytest.raises(HomeAssistantError, match="cyclic"):
        agent_config.validate_function_tools([tool])


async def test_legacy_date_quarantines_only_bad_tool_and_can_be_repaired(
    hass, management_agent, management_message, rules
):
    entry, subentry = management_agent
    broken = _tool("bad")
    broken["spec"]["parameters"]["default"] = date(2026, 11, 1)
    subentry.data = _options()
    subentry.data["functions"] = yaml.safe_dump([_tool("good"), broken])
    raw = subentry.data["functions"]
    state = function_configuration_repair_state(subentry.data)
    assert [item["name"] for item in state.invalid] == ["bad"]
    agent = _agent(hass, entry, subentry)
    assert "bad" not in {item["spec"]["name"] for item in agent._get_function_tools()}
    # Unrelated saves preserve the damaged raw definition and healthy siblings.
    saved = await _save(hass, entry, subentry, {"prompt": "Edited"})
    assert saved["valid"] and subentry.data["functions"] == raw
    assert [
        tool["spec"]["name"]
        for tool in _runtime_configured_function_tools(subentry.data)
    ] == ["good"]
    broken["spec"]["parameters"]["default"] = "2026-11-01"
    await ui.async_management_command(
        hass,
        "admin",
        True,
        management_message(
            "function_repair",
            "save_one",
            tool=broken,
            index=1,
            revision=ui.persisted_config_projection(subentry).revision,
        ),
    )
    assert function_configuration_repair_state(subentry.data) is None
    assert {"good", "bad"} <= {
        item["spec"]["name"] for item in agent._get_function_tools()
    }
    await _dispatch(agent)


async def _dispatch(agent, *, conditional=False):
    snapshot = request.build_provider_request_snapshot(
        agent.subentry.data, agent.entry.data, tools_required=True
    )
    mode = snapshot.api_mode
    stream = (
        _control_stream(
            mode,
            request.CONTINUE_CONVERSATION_TOOL_NAME,
            {"response": "Done", "continue_conversation": False},
        )
        if conditional
        else _final_stream()
        if mode == "responses"
        else FakeStream(
            [
                SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            delta=SimpleNamespace(
                                content="Done", refusal=None, tool_calls=None
                            ),
                            finish_reason="stop",
                        )
                    ],
                    usage=None,
                )
            ]
        )
    )
    create = AsyncMock(return_value=stream)
    agent.entry.runtime_data = SimpleNamespace(
        responses=SimpleNamespace(create=create),
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
    )
    lifecycle = begin_conversation_lifecycle()
    try:
        log = _chat_log(agent.hass)
        tools = agent._get_function_tools()
        await agent._async_handle_chat_log(
            log, tools, [], conditional_continue=conditional
        )
        assert final_assistant_text(log.content) == "Done"
    finally:
        end_conversation_lifecycle(lifecycle)
    return create.call_args.kwargs


@pytest.mark.parametrize(
    "provider,api,count,conditional,accepted",
    [
        ("azure", "chat_completions", 127, False, True),
        ("azure", "chat_completions", 127, True, False),
        ("azure", "chat_completions", 126, True, True),
        ("azure", "responses", 127, True, True),
        ("azure", "auto", 127, True, True),
        ("openai", "chat_completions", 127, True, True),
        ("openai", "responses", 127, True, True),
        ("openai", "auto", 127, True, True),
        ("custom", "chat_completions", 127, True, True),
    ],
)
async def test_accepted_save_reaches_actual_initial_provider_request(
    hass, management_agent, rules, provider, api, count, conditional, accepted
):
    entry, subentry = management_agent
    entry.data = {"api_provider": "azure" if provider == "azure" else "openai"}
    if provider == "custom":
        entry.data["base_url"] = "http://provider.invalid/v1"
    subentry.data = _options(count, api_mode=api)
    if provider == "azure":
        subentry.data["azure_model"] = "gpt-4o"
    old = deepcopy(subentry.data)
    saved = await _save(
        hass,
        entry,
        subentry,
        {"continue_conversation": "conditional" if conditional else "ha_default"},
    )
    assert saved["valid"] is accepted
    if not accepted:
        assert subentry.data == old
        assert "128-tool limit" in str(saved["errors"])
        # A rejected save leaves the previously working request usable.
        await _dispatch(_agent(hass, entry, subentry))
        return
    sent = await _dispatch(_agent(hass, entry, subentry), conditional=conditional)
    assert len(sent["tools"]) == count + 1 + int(conditional)
    json.dumps(sent)  # Full provider payload, not just a validator's return value.


@pytest.mark.parametrize(
    "enabled,always,extra,accepted",
    [
        (True, False, 125, True),
        (True, False, 126, False),
        (False, False, 126, True),
        (True, True, 126, False),
    ],
)
async def test_group_loader_counts_but_unloaded_members_do_not(
    hass, management_agent, rules, enabled, always, extra, accepted
):
    entry, subentry = management_agent
    entry.data = {"api_provider": "azure"}
    options = _options(
        extra + 20, azure_model="gpt-4o", continue_conversation="conditional"
    )
    options["function_groups"] = [
        {
            "id": "extras",
            "name": "Extras",
            "description": "Later",
            "functions": [f"marker_{i}" for i in range(extra, extra + 20)],
            "enabled": enabled,
            "loading_mode": "always" if always else "on_demand",
        }
    ]
    proposed = deepcopy(options["function_groups"])
    options["function_groups"][0]["enabled"] = False
    subentry.data = options
    saved = await _save(hass, entry, subentry, {"function_groups": proposed})
    if not accepted:
        assert saved["valid"] is False
        assert subentry.data["function_groups"][0]["enabled"] is False
        options["function_groups"] = proposed
        with pytest.raises(HomeAssistantError, match="128-tool limit"):
            request.validate_runtime_readiness(options, entry.data)
        return
    assert saved["valid"]
    sent = await _dispatch(_agent(hass, entry, subentry), conditional=True)
    assert len(sent["tools"]) == extra + 2 + int(enabled)


def test_readiness_is_independent_of_ambient_runtime_and_defers_dynamic_tools(hass):
    options = _options(127, azure_model="gpt-4o", continue_conversation="conditional")
    with (
        function_tool_runtime_scope(
            function_tools_supported=False, group_loader_supported=False
        ),
        pytest.raises(HomeAssistantError, match="128-tool limit"),
    ):
        request.validate_runtime_readiness(options, {"api_provider": "azure"})
    # Scope-dependent data and discovered HA tools are not guaranteed initial tools.
    options = _options(
        126,
        azure_model="gpt-4o",
        continue_conversation="conditional",
        memory_mode="manual",
        temporary_memory="eager",
        knowledge_enabled=True,
    )
    before = deepcopy(options)
    request.validate_runtime_readiness(options, {"api_provider": "azure"})
    assert options == before
    dynamic = [_tool(f"discovered_{i}") for i in range(10)]
    with pytest.raises(HomeAssistantError, match="128-tool limit"):
        request.validate_provider_tool_count(
            128 + len(dynamic), "azure", "chat_completions"
        )


@pytest.mark.parametrize("group", [False, True])
async def test_trusted_availability_mutations_cannot_publish_over_limit(
    hass, management_agent, monkeypatch, group
):
    entry, subentry = management_agent
    entry.data = {"api_provider": "azure"}
    subentry.data = _options(
        127, azure_model="gpt-4o", continue_conversation="conditional"
    )
    tools = yaml.safe_load(subentry.data["functions"])
    if group:
        subentry.data["function_groups"] = [
            {
                "id": "later",
                "name": "Later",
                "description": "Later tools",
                "functions": ["marker_126"],
                "loading_mode": "on_demand",
                "enabled": False,
            }
        ]
    else:
        tools[-1]["enabled"] = False
        subentry.data["functions"] = yaml.safe_dump(tools)
    original = deepcopy(subentry.data)
    monkeypatch.setattr(
        services,
        "resolve_memory_agent",
        lambda *_: (entry.entry_id, subentry.subentry_id),
    )
    mutate = (
        services.async_set_function_groups_enabled
        if group
        else services.async_set_function_tools_enabled
    )
    with pytest.raises(HomeAssistantError, match="128-tool limit"):
        await mutate(
            hass,
            entry.entry_id,
            subentry.subentry_id,
            ["later" if group else "marker_126"],
            True,
        )
    assert subentry.data == original
    hass.config_entries.async_update_subentry.assert_not_called()
    await _dispatch(_agent(hass, entry, subentry), conditional=True)


@pytest.mark.parametrize("conditional", [False, True])
async def test_zero_budget_can_save_large_dormant_catalogue(
    hass, management_agent, rules, conditional
):
    entry, subentry = management_agent
    entry.data = {"api_provider": "azure"}
    subentry.data = _options(
        140, azure_model="gpt-4o", max_function_calls_per_conversation=0
    )
    saved = await _save(
        hass,
        entry,
        subentry,
        {"continue_conversation": "conditional" if conditional else "ha_default"},
    )
    assert saved["valid"]
    sent = await _dispatch(_agent(hass, entry, subentry), conditional=conditional)
    assert len(sent.get("tools", [])) == int(conditional)


async def test_tool_editor_rejects_new_guaranteed_tool_but_allows_disabled_tool(
    hass, management_agent, management_message, rules
):
    entry, subentry = management_agent
    entry.data = {"api_provider": "azure"}
    subentry.data = _options(
        126, azure_model="gpt-4o", continue_conversation="conditional"
    )
    candidate = _tool("extra")
    with pytest.raises(HomeAssistantError, match="128-tool limit"):
        await ui.async_management_command(
            hass,
            "admin",
            True,
            management_message(
                "tools",
                "save",
                tool=candidate,
                revision=ui.persisted_config_projection(subentry).revision,
            ),
        )
    candidate["enabled"] = False
    await ui.async_management_command(
        hass,
        "admin",
        True,
        management_message(
            "tools",
            "save",
            tool=candidate,
            revision=ui.persisted_config_projection(subentry).revision,
        ),
    )
    sent = await _dispatch(_agent(hass, entry, subentry), conditional=True)
    assert len(sent["tools"]) == 128


async def test_dynamic_group_growth_is_checked_at_real_runtime_boundary(
    hass, management_agent, rules
):
    from dataclasses import replace

    from custom_components.extended_openai_conversation_responses import conversation
    from custom_components.extended_openai_conversation_responses.function_groups import (
        FunctionGroupSession,
    )

    entry, subentry = management_agent
    entry.data = {"api_provider": "azure"}
    subentry.data = _options(
        145,
        azure_model="gpt-4o",
        continue_conversation="conditional",
        function_groups=[
            {
                "id": "later",
                "name": "Later",
                "description": "Optional",
                "functions": [f"marker_{i}" for i in range(125, 145)],
                "loading_mode": "on_demand",
            }
        ],
    )
    assert (await _save(hass, entry, subentry, {"prompt": "Valid initial request"}))[
        "valid"
    ]
    agent = _agent(hass, entry, subentry)
    assert len((await _dispatch(agent, conditional=True))["tools"]) == 128
    session = FunctionGroupSession("test", 0)
    token = conversation._ACTIVE_TURN.set(
        replace(conversation._ACTIVE_TURN.get(), function_group_session=session)
    )
    lifecycle = begin_conversation_lifecycle()
    try:
        refused = agent._load_function_groups(["later"])
        assert refused["status"] == "error" and not session.loaded_group_ids
        # Even unexpected retained growth cannot bypass the final dispatch guard.
        session.loaded_group_ids.add("later")
        with pytest.raises(HomeAssistantError, match="128-tool limit"):
            await _dispatch(agent, conditional=True)
        agent.entry.runtime_data.chat.completions.create.assert_not_called()
    finally:
        end_conversation_lifecycle(lifecycle)
        conversation._ACTIVE_TURN.reset(token)


async def test_undiscovered_ha_tools_and_skills_do_not_reject_safe_save(
    hass, management_agent, rules, monkeypatch
):
    from custom_components.extended_openai_conversation_responses.ha_llm_tools import (
        new_reference_tool,
    )
    from custom_components.extended_openai_conversation_responses.skill_availability import (
        CANONICAL_SKILL_LOADER_PATH,
    )
    from custom_components.extended_openai_conversation_responses.skills import (
        SkillManager,
    )

    entry, subentry = management_agent
    entry.data = {"api_provider": "azure"}
    remote = new_reference_tool(
        {
            "type": "ha_llm",
            "source_type": "api",
            "source_id": "absent",
            "api_id": "absent",
            "tool_name": "remote",
        },
        set(),
    )
    loader = _tool("load_skill")
    loader["function"] = {"type": "read_file", "path": CANONICAL_SKILL_LOADER_PATH}
    subentry.data = _options(
        functions=[*[_tool(f"marker_{i}") for i in range(126)], remote, loader],
        azure_model="gpt-4o",
        continue_conversation="conditional",
        function_groups=[
            {
                "id": "discovered",
                "name": "Discovered",
                "description": "Optional",
                "functions": [remote["spec"]["name"], "load_skill"],
                "loading_mode": "on_demand",
            }
        ],
    )
    monkeypatch.setattr(
        SkillManager, "get_loaded_instance", classmethod(lambda _cls: None)
    )
    assert (await _save(hass, entry, subentry, {"prompt": "Safe until discovery"}))[
        "valid"
    ]
    sent = await _dispatch(_agent(hass, entry, subentry), conditional=True)
    assert len(sent["tools"]) == 128


@pytest.mark.parametrize("operation", ["duplicate", "import_preview", "import"])
@pytest.mark.parametrize("accepted", [False, True])
async def test_transfer_creation_boundary_agrees_with_actual_request(
    hass, management_agent, management_message, rules, operation, accepted
):
    entry, subentry = management_agent
    entry.data = {"api_provider": "azure"}
    subentry.data = _options(
        126 if accepted else 127,
        azure_model="gpt-4o",
        continue_conversation="conditional",
    )
    message = management_message("configuration", operation)
    if operation != "duplicate":
        from tests.helpers import legacy_agent_export

        message.update(document=legacy_agent_export(subentry), mode="new")
    if not accepted:
        with pytest.raises(HomeAssistantError, match="128-tool limit"):
            await ui.async_management_command(hass, "admin", True, message)
        hass.config_entries.async_add_subentry.assert_not_called()
        return
    saved = await ui.async_management_command(hass, "admin", True, message)
    if operation == "import_preview":
        candidate = SimpleNamespace(subentry_id="preview", data=saved["config"])
        hass.config_entries.async_add_subentry.assert_not_called()
    else:
        candidate = hass.config_entries.async_add_subentry.call_args.args[1]
    sent = await _dispatch(_agent(hass, entry, candidate), conditional=True)
    assert len(sent["tools"]) == 128


async def test_prepared_restore_rechecks_readiness_before_journaling(
    hass, management_agent, monkeypatch
):
    from custom_components.extended_openai_conversation_responses import (
        backup,
        restore_recovery,
    )
    from tests.restore_participants import participants
    from tests.test_backup import _document
    from tests.test_restore_recovery import MemoryJournalStore

    entry, subentry = management_agent
    entry.data = {"api_provider": "azure"}
    prepared = backup.inspect_backup(_document(), subentry.subentry_id)
    rollback = deepcopy(prepared)
    prepared.config = _options(
        127, azure_model="gpt-4o", continue_conversation="conditional"
    )
    journal = MemoryJournalStore()
    apply = AsyncMock()
    monkeypatch.setattr(restore_recovery, "_journal_store", lambda *_: journal)
    monkeypatch.setattr(
        restore_recovery, "_durable_managers", AsyncMock(return_value=participants())
    )
    monkeypatch.setattr(
        backup, "_snapshot_for_restore", AsyncMock(return_value=rollback)
    )
    monkeypatch.setattr(restore_recovery, "_apply_prepared", apply)
    with pytest.raises(backup.BackupError, match="128-tool limit"):
        await restore_recovery._async_restore_backup_recoverably(
            hass, entry, subentry, prepared
        )
    assert journal.saved == []
    apply.assert_not_called()
    # A corrected restore commits through the existing journal/update path and
    # its published configuration reaches the real initial request assembler.
    prepared.config = _options(
        126, azure_model="gpt-4o", continue_conversation="conditional"
    )
    monkeypatch.setattr(restore_recovery, "_async_persist_config_entries", AsyncMock())
    monkeypatch.setattr(restore_recovery, "reset_restored_runtime", lambda *_args: None)
    await restore_recovery._async_restore_backup_recoverably(
        hass, entry, subentry, prepared
    )
    assert journal.removed == 1
    sent = await _dispatch(_agent(hass, entry, subentry), conditional=True)
    assert len(sent["tools"]) == 128


async def test_provider_owned_tools_are_present_in_accepted_runtime_request(
    hass, management_agent, rules
):
    entry, subentry = management_agent
    entry.data = {"api_provider": "openai"}
    subentry.data = _options(1, api_mode="responses")
    assert (await _save(hass, entry, subentry, {"web_search": True}))["valid"]
    sent = await _dispatch(_agent(hass, entry, subentry))
    assert len(sent["tools"]) == 3
    assert sent["tools"][0]["type"] == "web_search"


async def test_archive_search_and_guest_generated_tools_are_counted(
    hass, management_agent, rules
):
    from custom_components.extended_openai_conversation_responses.conversation_archive import (
        archive_tools,
    )

    entry, subentry = management_agent
    entry.data = {"api_provider": "azure"}
    count = 128 - 2 - len(archive_tools())
    subentry.data = _options(
        count,
        azure_model="gpt-4o",
        continue_conversation="conditional",
        archive_enabled=True,
    )
    assert (await _save(hass, entry, subentry, {"archive_model_search_enabled": True}))[
        "valid"
    ]
    sent = await _dispatch(_agent(hass, entry, subentry), conditional=True)
    assert len(sent["tools"]) == 128
    rejected = await _save(hass, entry, subentry, {"guest_mode_enabled": True})
    assert rejected["valid"] is False
    await _dispatch(_agent(hass, entry, subentry), conditional=True)


async def test_real_ha_script_keeps_typed_backend_values(tmp_path, monkeypatch):
    """Use genuine HA Template/Script/service dispatch, without its POSIX test runner."""
    real = HomeAssistant(str(tmp_path))
    real.config.legacy_templates = False
    monkeypatch.setattr(cv, "_async_get_hass_or_none", lambda: real)
    observed = []

    async def record(call):
        observed.append(call.data["day"])

    real.services.async_register("readiness_probe", "record", record)
    tool = _tool()
    tool["function"] = {
        "type": "script",
        "variables": {"day": date(2026, 11, 1)},
        "sequence": [
            {
                "action": "readiness_probe.record",
                "data": {"day": "{{ day.isoformat() }}"},
            }
        ],
    }
    try:
        options = _options(
            functions=[tool, *[_tool(f"other_{i}") for i in range(125)]],
            azure_model="gpt-4o",
            continue_conversation="conditional",
        )
        entry = SimpleNamespace(entry_id="real", data={"api_provider": "azure"})
        request.validate_runtime_readiness(options, entry.data)
        subentry = SimpleNamespace(subentry_id="real-agent", data=options)
        agent = _agent(real, entry, subentry)
        configured = agent._get_configured_function_tools()[0]
        await agent._execute_function_tool(
            configured,
            llm.ToolInput(id="script", tool_name="marker", tool_args={}, external=True),
            None,
            [],
        )
        assert observed == ["2026-11-01"]
        sent = await _dispatch(agent, conditional=True)
        assert len(sent["tools"]) == 128
        json.dumps(sent)
    finally:
        await real.async_stop(force=True)
