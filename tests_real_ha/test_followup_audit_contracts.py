"""Follow-up findings through native HA services, storage and translations."""

import pytest
from pytest_homeassistant_custom_component.common import MockUser
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.helpers.translation import _TranslationCache

from custom_components.extended_openai_conversation_responses.const import (
    DOMAIN,
    CONF_FUNCTION_TOOLS,
)
from custom_components.extended_openai_conversation_responses import transfer
from tests_real_ha.test_cross_feature_acceptance import _agent, _provider
from tests_real_ha.test_request_rules_script_semantics import _local
from tests_real_ha.test_service_registry_acceptance import _response_service_call


@pytest.mark.parametrize("api_mode", ["chat_completions", "responses"])
@pytest.mark.parametrize("decision", [False, True])
@pytest.mark.parametrize("answer", ["", "Done"], ids=["silent", "spoken"])
async def test_native_finalizer_after_action_terminates(hass, api_mode, decision, answer):
    from tests.test_conditional_finalizer_termination import exercise_finalizer_termination

    await exercise_finalizer_termination(hass, api_mode, decision, answer)


@pytest.mark.parametrize("streaming", [True, False], ids=["streamed", "completed"])
@pytest.mark.parametrize("answer", ["", None], ids=["empty", "null"])
async def test_native_normal_silent_chat_after_action_terminates(
    hass, monkeypatch, streaming, answer
):
    from tests.test_normal_chat_completion_termination import (
        exercise_normal_chat_completion,
    )

    await exercise_normal_chat_completion(hass, monkeypatch, streaming, answer)


@pytest.mark.parametrize("streaming", [True, False], ids=["streamed", "completed"])
async def test_native_no_output_responses_after_action_fails_boundedly(
    hass, monkeypatch, streaming
):
    from tests.test_empty_responses_completion import (
        exercise_empty_responses_completion,
    )

    await exercise_empty_responses_completion(hass, monkeypatch, streaming)


@pytest.mark.parametrize("child_service", ["conversation.process", f"{DOMAIN}.process"])
async def test_standard_child_service_does_not_poison_parent_response(
    hass, monkeypatch, child_service
):
    child = await _agent(hass, title="Child")
    await child._request_rules.async_create(
        _local(
            [{"variables": {"audit": "child"}}],
            phrase="child request",
            success="Child local answer",
        )
    )
    async_expose_entity(hass, "conversation", child.entity_id, True)
    tool = {
        "spec": {
            "name": "delegate",
            "description": "Ask the child",
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {
            "type": "script",
            "sequence": [
                {
                    "action": child_service,
                    "data": {
                        "agent_id": child.entity_id,
                        "text": "child request",
                        "language": "en",
                    },
                    "response_variable": "child_answer",
                }
            ],
        },
    }
    parent = await _agent(hass, title="Parent", **{CONF_FUNCTION_TOOLS: [tool]})
    _provider(monkeypatch, child, [])
    sent = _provider(
        monkeypatch,
        parent,
        [
            {
                "index": 0,
                "id": "delegate-call",
                "type": "function",
                "function": {"name": "delegate", "arguments": "{}"},
            },
            "Parent provider answered",
        ],
    )
    user = MockUser(id="nested-owner", name="Nested owner", is_owner=True).add_to_hass(
        hass
    )
    result = await _response_service_call(
        hass,
        "process",
        {"text": "Ask the child", "agent_id": parent.entity_id},
        user_id=user.id,
    )
    assert result["successful"] is True, result
    assert result["response"] == "Parent provider answered", result
    assert len(sent) == 2
    assert result["handled_locally"] is False
    assert not result.get("matched_rule")
    assert len(child._usage.runs) == 1 and child._usage.runs[0].request_count == 0


async def test_native_saved_rule_exports_no_encoded_credentials(hass):
    agent = await _agent(hass)
    secret = "synthetic-audit-token-do-not-use"
    await agent._request_rules.async_create(
        _local(
            [{"action": "rest_command.report_measurement", "data": {"api_key": secret}}]
        )
    )
    saved = await agent._request_rules.async_backup_data()
    document = transfer._new_transfer_document(agent.entry, agent.subentry, "setup")
    document["sections"] = {"configuration": {}, "request_rules": saved}
    assert secret not in transfer.finalize_setup_export(document)["json"]
    assert secret in saved["rules"][0]["action"]["canonical_signature"]
    assert await agent._request_rules.async_backup_data() == saved


@pytest.mark.parametrize("language", ["en", "de"])
async def test_runtime_retirement_translation_and_english_fallback(hass, language):
    cache = _TranslationCache(hass)
    assert await cache.async_fetch(language, "config", {DOMAIN})
    issues = await cache.async_fetch(language, "issues", {DOMAIN})
    prefix = f"component.{DOMAIN}.issues.retired_model."
    assert issues[prefix + "title"]
    assert all(
        "{" + name + "}" in issues[prefix + "description"]
        for name in ("assistant", "model", "shutdown_at")
    )


@pytest.mark.parametrize("child_service", ["conversation.process", f"{DOMAIN}.process"])
@pytest.mark.parametrize("loading_mode", ["always", "on_demand"])
async def test_nested_rule_uses_callee_group_and_restores_parent_group(
    hass, monkeypatch, child_service, loading_mode
):
    """Real Rule Function dispatch must not compare another agent's Groups."""
    import json

    from custom_components.extended_openai_conversation_responses.const import (
        CONF_FUNCTION_GROUPS,
        SERVICE_CALL_FUNCTION,
    )

    def template(name, value):
        return {
            "spec": {
                "name": name,
                "description": name,
                "parameters": {"type": "object", "properties": {}},
            },
            "function": {
                "type": "template",
                "value_template": value,
                "parse_result": False,
            },
        }

    def group(group_id, names, mode="always"):
        return {
            "id": group_id,
            "name": group_id,
            "description": group_id,
            "enabled": True,
            "loading_mode": mode,
            "functions": names,
        }

    child = await _agent(
        hass,
        title="Grouped child",
        **{
            CONF_FUNCTION_TOOLS: [template("surface_value_a", "child-only-value")],
            CONF_FUNCTION_GROUPS: [
                group("child-group", ["surface_value_a"], loading_mode)
            ],
        },
    )
    effects = []

    async def record(call):
        effects.append(call.data["value"])

    hass.services.async_register("nested_group_probe", "record", record)
    await child._request_rules.async_create(
        _local(
            [
                {
                    "action": f"{DOMAIN}.{SERVICE_CALL_FUNCTION}",
                    "data": {
                        "function": "surface_value_a",
                        "arguments": {},
                        "result_alias": "answer",
                    },
                },
                {"action": "nested_group_probe.record", "data": {"value": "{answer}"}},
            ],
            phrase="grouped child request",
            success="Child value {answer}",
            failure="Child function failed",
        )
    )
    async_expose_entity(hass, "conversation", child.entity_id, True)
    child_sent = _provider(monkeypatch, child, [])
    delegate = template("delegate", "unused")
    delegate["function"] = {
        "type": "script",
        "sequence": [
            {
                "action": child_service,
                "data": {
                    "agent_id": child.entity_id,
                    "text": "grouped child request",
                    "language": "en",
                },
                "response_variable": "child_answer",
            },
            {"variables": {"_function_result": "{{ child_answer }}"}},
        ],
    }
    parent = await _agent(
        hass,
        title="Grouped parent",
        **{
            CONF_FUNCTION_TOOLS: [
                delegate,
                template("parent_value", "parent-only-value"),
            ],
            CONF_FUNCTION_GROUPS: [group("parent-group", ["delegate", "parent_value"])],
        },
    )
    parent_sent = _provider(
        monkeypatch,
        parent,
        [
            {
                "index": 0,
                "id": name + "-call",
                "type": "function",
                "function": {"name": name, "arguments": "{}"},
            }
            for name in ("delegate", "parent_value")
        ]
        + ["Parent completed"],
    )
    user = MockUser(id="group-owner", name="Group owner", is_owner=True).add_to_hass(
        hass
    )
    direct = await _response_service_call(
        hass,
        "process",
        {"text": "grouped child request", "agent_id": child.entity_id},
        user_id=user.id,
    )
    assert direct["successful"] is True, direct
    assert direct["response"] == "Child value child-only-value"
    result = await _response_service_call(
        hass,
        "process",
        {"text": "Delegate and check parent", "agent_id": parent.entity_id},
        user_id=user.id,
    )
    assert result["successful"] is True, result
    assert result["response"] == "Parent completed"
    assert effects == ["child-only-value", "child-only-value"]
    assert child_sent == []
    assert len(parent_sent) == 3
    retained = json.dumps(parent_sent[-1]["messages"])
    assert "Child value child-only-value" in retained
    assert "parent-only-value" in retained
    assert "Child function failed" not in retained


@pytest.mark.parametrize("quarantined_tool", [False, True])
async def test_group_repair_reaches_registered_websocket_handler(
    hass, hass_ws_client, monkeypatch, quarantined_tool
):
    from copy import deepcopy
    from unittest.mock import AsyncMock
    from custom_components.extended_openai_conversation_responses import (
        management_ui as ui,
    )
    from tests_real_ha.test_management_backend_acceptance import (
        _admin_client,
        _management_call,
        _management_response,
    )

    tool = {
        "spec": {
            "name": "execute_services",
            "description": "Probe",
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {"type": "template", "value_template": "OK"},
    }
    agent = await _agent(hass, **{CONF_FUNCTION_TOOLS: [tool]})
    broken_tool = deepcopy(tool)
    if quarantined_tool:
        broken_tool["spec"]["parameters"]["description"] = 123
    broken_group = {
        "id": "repair-group",
        "description": "Repair me",
        "loading_mode": "always",
        "enabled": True,
        "functions": ["execute_services"],
    }
    hass.config_entries.async_update_subentry(
        agent.entry,
        agent.subentry,
        data={
            **agent.subentry.data,
            CONF_FUNCTION_TOOLS: [broken_tool],
            "function_groups": [broken_group],
        },
    )
    client = await _admin_client(hass, hass_ws_client)
    before = await _management_call(
        client, entry=agent.entry, section="configuration", action="get"
    )
    assert before.get("function_repair")
    raw_tools = deepcopy(agent.subentry.data[CONF_FUNCTION_TOOLS])
    dispatch = AsyncMock(wraps=ui.async_management_command)
    monkeypatch.setattr(ui, "async_management_command", dispatch)
    replacement = {**broken_group, "name": "Repaired", "functions": []}
    repaired = await _management_response(
        client,
        entry=agent.entry,
        section="tools",
        action="save_group",
        original_id="repair-group",
        repair=True,
        group=replacement,
    )
    assert repaired["success"] is True, repaired
    assert repaired["result"].get("valid", True), repaired
    assert dispatch.await_count == 1
    assert agent.subentry.data["function_groups"][0]["name"] == "Repaired"
    assert agent.subentry.data[CONF_FUNCTION_TOOLS] == raw_tools
    missing_id = await _management_response(
        client,
        entry=agent.entry,
        section="tools",
        action="save_group",
        repair=True,
        group=replacement,
    )
    assert missing_id["success"] is False
    assert missing_id["error"]["code"] == "invalid_request"
    assert "original_id" in missing_id["error"]["message"]
    assert dispatch.await_count == 2
    invalid_flag = await _management_response(
        client,
        entry=agent.entry,
        section="tools",
        action="save_group",
        original_id="repair-group",
        repair="true",
        group=replacement,
    )
    assert invalid_flag["success"] is False
    assert invalid_flag["error"]["code"] == "invalid_format"
    assert dispatch.await_count == 2
    after = await _management_call(
        client, entry=agent.entry, section="configuration", action="get"
    )
    assert after["config"]["function_groups"][0]["name"] == "Repaired"


@pytest.mark.parametrize("commentary", [False, True])
async def test_native_ha_reasoning_finalizer_history(hass, commentary):
    from tests.test_responses_history_coherence import (
        exercise_reasoning_finalizer_history,
    )

    await exercise_reasoning_finalizer_history(hass, commentary)


async def test_native_ha_interrupted_reasoning_history(hass):
    from tests.test_responses_history_coherence import (
        exercise_interrupted_response_history,
    )

    await exercise_interrupted_response_history(hass)


@pytest.mark.parametrize("persisted", [False, True], ids=["structured", "yaml"])
@pytest.mark.parametrize("exporter", ["setup", "legacy"])
async def test_native_http_params_and_template_exports_never_render_or_leak(
    hass, monkeypatch, persisted, exporter
):
    from copy import deepcopy
    import json
    import yaml
    from homeassistant.helpers.template import Template
    from custom_components.extended_openai_conversation_responses import backup
    from tests.test_http_parameter_template_redaction import (
        ALIASES,
        SECRET,
        configuration,
    )

    rest = configuration("params-key")["functions"][0]
    rest["spec"]["name"] = "weather_params"
    rest["function"]["params"] = {key: SECRET for key in ALIASES} | {"units": "c"}
    scrape = configuration("jinja-userinfo", function_type="scrape")["functions"][0]
    scrape["spec"]["name"] = "weather_template"
    tools = [rest, scrape]
    agent = await _agent(
        hass, **{CONF_FUNCTION_TOOLS: yaml.safe_dump(tools) if persisted else tools}
    )
    original = deepcopy(dict(agent.subentry.data))

    def never_render(*_args, **_kwargs):
        raise AssertionError("Exports must not execute the configured HTTP templates")

    monkeypatch.setattr(Template, "async_render", never_render)
    private = backup.private_configuration_snapshot(agent.subentry.data)
    assert SECRET in private["functions"]
    if exporter == "setup":
        result = await transfer.async_create_setup_export(
            hass, agent.entry, agent.subentry
        )
    else:
        result = await backup.async_create_backup(hass, agent.entry, agent.subentry)
    assert SECRET not in result["json"]
    assert SECRET not in json.dumps(result["document"])
    assert agent.subentry.data == original
    assert backup.private_configuration_snapshot(agent.subentry.data) == private

async def test_malformed_rest_xml_returns_assist_error_after_completed_effect(
    hass, monkeypatch, aiohttp_server, socket_enabled
):
    from aiohttp import web
    from custom_components.extended_openai_conversation_responses.const import (
        CONF_FUNCTION_TOOL_ERROR_RECOVERY,
    )
    from tests_real_ha.test_cross_feature_acceptance import _say, _speech

    async def malformed(_request):
        return web.Response(
            text="<root><broken></root>", content_type="application/xml"
        )

    app = web.Application()
    app.router.add_get("/malformed", malformed)
    server = await aiohttp_server(app)
    effects = []

    async def record(call):
        effects.append(call.context.id)

    hass.services.async_register("rest_boundary_probe", "record", record)

    def tool(name, function):
        return {
            "spec": {
                "name": name,
                "description": name,
                "parameters": {"type": "object", "properties": {}},
            },
            "function": function,
        }

    agent = await _agent(
        hass,
        **{
            CONF_FUNCTION_TOOL_ERROR_RECOVERY: False,
            CONF_FUNCTION_TOOLS: [
                tool(
                    "record_effect",
                    {
                        "type": "script",
                        "sequence": [{"action": "rest_boundary_probe.record"}],
                    },
                ),
                tool(
                    "fetch_xml",
                    {
                        "type": "rest",
                        "resource": str(server.make_url("/malformed")),
                        "value_template": "{{ value_json.required }}",
                    },
                ),
            ],
        },
    )

    def call(name):
        return {
            "index": 0,
            "id": name + "-call",
            "type": "function",
            "function": {"name": name, "arguments": "{}"},
        }

    sent = _provider(
        monkeypatch, agent, [call("record_effect"), call("fetch_xml"), "Recovered."]
    )
    result = await _say(hass, agent, "Record then fetch")
    assert result.response.error_code is not None
    assert "REST response parsing failed: ExpatError" in _speech(
        result, successful=False
    )
    assert "<root>" not in _speech(result, successful=False)
    assert len(sent) == 2
    assert len(effects) == 1
    recovered = await _say(hass, agent, "Another turn")
    assert _speech(recovered) == "Recovered."
    assert len(sent) == 3
    assert len(effects) == 1

@pytest.mark.parametrize("child_service", ["conversation.process", f"{DOMAIN}.process"])
@pytest.mark.parametrize("guest_policy", ["on", "custom"])
async def test_delegated_guest_child_retains_its_allowed_catalogue(
    hass, monkeypatch, child_service, guest_policy
):
    from tests_real_ha.test_cross_feature_acceptance import _say, _speech
    from custom_components.extended_openai_conversation_responses.const import (
        CONF_GUEST_POLICY_VERSION, GUEST_POLICY_VERSION,
    )

    child_tool = {
        "spec": {
            "name": "child_history",
            "description": "Read history",
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {"type": "native", "name": "get_history"},
    }
    child = await _agent(
        hass,
        title="Guest child",
        **{
            CONF_FUNCTION_TOOLS: [child_tool],
            CONF_GUEST_POLICY_VERSION: GUEST_POLICY_VERSION,
            "guest_mode_enabled": True,
            "guest_function_policy": guest_policy,
            "guest_allowed_function_names": ["child_history"],
        },
    )
    await child._guest_mode.async_update_trusted(indefinite=True)
    assert child._guest_mode.status()["currently_active"]
    async_expose_entity(hass, "conversation", child.entity_id, True)
    delegate = {
        "spec": {
            "name": "delegate",
            "description": "Ask child",
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {
            "type": "script",
            "sequence": [
                {
                    "action": child_service,
                    "data": {
                        "agent_id": child.entity_id,
                        "text": "child request",
                        "language": "en",
                    },
                    "response_variable": "answer",
                }
            ],
        },
    }
    parent = await _agent(
        hass, title="Guest parent", **{CONF_FUNCTION_TOOLS: [delegate]}
    )
    child_sent = _provider(monkeypatch, child, ["Direct answer", "Nested answer"])
    assert _speech(await _say(hass, child, "child request")) == "Direct answer"
    parent_sent = _provider(
        monkeypatch,
        parent,
        [
            {
                "index": 0,
                "id": "delegate-call",
                "type": "function",
                "function": {"name": "delegate", "arguments": "{}"},
            },
            "Parent answer",
        ],
    )
    assert _speech(await _say(hass, parent, "Delegate")) == "Parent answer"
    assert len(child_sent) == 2

    def names(request):
        return {tool["function"]["name"] for tool in request["tools"]}

    assert "child_history" in names(child_sent[0])
    assert names(child_sent[0]) == names(child_sent[1])
    assert "delegate" in names(parent_sent[-1])
    assert "child_history" not in names(parent_sent[-1])


@pytest.mark.parametrize("kind", ["tools", "groups"])
@pytest.mark.parametrize("enabled", [False, True])
async def test_registered_toggle_services_preserve_quarantined_siblings(
    hass, kind, enabled
):
    from copy import deepcopy
    import yaml
    from homeassistant.core import Context

    healthy = {
        "spec": {
            "name": "healthy",
            "description": "Healthy",
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {"type": "template", "value_template": "ok"},
        "enabled": not enabled,
    }
    legacy = deepcopy(healthy)
    legacy["spec"]["name"] = "legacy_script"
    legacy["function"] = {
        "type": "script",
        "mode": "single",
        "sequence": [{"delay": 1}],
    }
    group = {
        "id": "healthy-group",
        "name": "Healthy",
        "description": "Healthy tools",
        "loading_mode": "always",
        "enabled": not enabled,
        "functions": ["healthy"],
    }
    agent = await _agent(
        hass,
        **{
            CONF_FUNCTION_TOOLS: yaml.safe_dump([healthy, legacy]),
            "function_groups": [group],
        },
    )
    service = ("enable_" if enabled else "disable_") + "function_" + kind
    await hass.services.async_call(
        DOMAIN,
        service,
        {
            "config_entry": agent.entry.entry_id,
            "agent_id": agent.entity_id,
            "functions" if kind == "tools" else "function_groups": [
                "healthy" if kind == "tools" else "healthy-group"
            ],
        },
        blocking=True,
        context=Context(),
    )
    saved = yaml.safe_load(agent.subentry.data[CONF_FUNCTION_TOOLS])
    assert (
        next(tool for tool in saved if tool["spec"]["name"] == "legacy_script")
        == legacy
    )
    if kind == "tools":
        assert (
            next(tool for tool in saved if tool["spec"]["name"] == "healthy")["enabled"]
            is enabled
        )
    else:
        assert agent.subentry.data["function_groups"][0]["enabled"] is enabled


async def test_cold_multi_agent_websocket_get_reuses_worker_yaml(
    hass, hass_ws_client, monkeypatch
):
    import threading
    import yaml
    from custom_components.extended_openai_conversation_responses import (
        management_function_repair as repair,
        management_ui as ui,
    )
    from tests_real_ha.test_management_backend_acceptance import (
        _admin_client,
        _management_call,
    )

    tools = [
        {
            "spec": {
                "name": f"cold_history_{index}",
                "description": "Read history",
                "parameters": {
                    "type": "object",
                    "properties": {
                        f"field_{number}": {
                            "type": "string",
                            "description": "Detailed valid schema " * 5,
                        }
                        for number in range(30)
                    },
                },
            },
            "function": {"type": "native", "name": "get_history"},
        }
        for index in range(12)
    ]
    agent = await _agent(
        hass, title="Cold target", **{CONF_FUNCTION_TOOLS: yaml.safe_dump(tools)}
    )
    await _agent(hass, title="Second saved agent")
    client = await _admin_client(hass, hass_ws_client)
    ui._configuration_defaults()
    repair.discard_persisted_config_projection(agent.subentry)
    # Startup prewarming now shares pure decoding with runtime preparation.
    # Keep this a genuinely cold projection/decode boundary test.
    from custom_components.extended_openai_conversation_responses import agent_config

    agent_config._decoded_function_tools.cache_clear()
    raw = agent.subentry.data[CONF_FUNCTION_TOOLS]
    main_thread = threading.get_ident()
    decode_threads, validation_threads = [], []
    original_load, original_snapshot = yaml.safe_load, repair.agent_config_snapshot

    def load(value):
        if value == raw:
            decode_threads.append(threading.get_ident())
        return original_load(value)

    def snapshot(*args, **kwargs):
        validation_threads.append(threading.get_ident())
        return original_snapshot(*args, **kwargs)

    monkeypatch.setattr(yaml, "safe_load", load)
    monkeypatch.setattr(repair, "agent_config_snapshot", snapshot)
    result = await _management_call(
        client, entry=agent.entry, section="configuration", action="get"
    )
    assert len(result["config"]["functions"]) == 12
    assert len(decode_threads) == 1 and decode_threads[0] != main_thread
    assert validation_threads == [main_thread]
    await _management_call(
        client, entry=agent.entry, section="configuration", action="get"
    )
    assert len(decode_threads) == 1


async def test_native_options_flow_reports_legacy_input_schema(hass, monkeypatch):
    import yaml
    from tests_real_ha.test_cross_feature_acceptance import _provider

    bad = {
        "spec": {
            "name": "old_phone",
            "description": "Legacy phone tool",
            "parameters": {
                "type": "object",
                "properties": {"phone": {"type": "string", "minLength": "1"}},
            },
        },
        "function": {"type": "template", "value_template": "ok"},
    }
    agent = await _agent(hass, **{CONF_FUNCTION_TOOLS: yaml.safe_dump([bad])})
    sent = _provider(monkeypatch, agent, [])
    menu = await hass.config_entries.options.async_init(agent.entry.entry_id)
    selection = await hass.config_entries.options.async_configure(
        menu["flow_id"], {"next_step_id": "test_agent"}
    )
    result = await hass.config_entries.options.async_configure(
        selection["flow_id"], {"agent_id": agent.subentry.subentry_id}
    )
    assert result["step_id"] == "test_result"
    assert not sent
    report = result["data_schema"]({})["report"]
    assert "Configuration: Failed" in report and "minLength" in report


@pytest.mark.parametrize("conditional", [False, True])
async def test_zero_budget_request_never_constructs_external_api(
    hass, monkeypatch, conditional
):
    from homeassistant.helpers import llm
    from tests_real_ha.test_cross_feature_acceptance import _say, _speech

    calls = []

    class DormantAPI(llm.API):
        def __init__(self):
            super().__init__(hass=hass, id="dormant-source", name="Dormant source")

        async def async_get_api_instance(self, context):
            calls.append(context)
            raise AssertionError("Dormant external API must not be constructed")

    llm.async_register_api(hass, DormantAPI())
    tool = {
        "spec": {"name": "external"},
        "enabled": True,
        "function": {
            "type": "ha_llm",
            "source_type": "api",
            "source_id": "external-implementation",
            "api_id": "dormant-source",
            "tool_name": "external",
        },
    }
    agent = await _agent(
        hass,
        **{
            CONF_FUNCTION_TOOLS: [tool],
            "max_function_calls_per_conversation": 0,
            "continue_conversation": "conditional" if conditional else "ha_default",
        },
    )
    replies = (
        [
            {
                "index": 0,
                "id": "finish",
                "type": "function",
                "function": {
                    "name": "set_continue_conversation",
                    "arguments": '{"response":"Done","continue_conversation":false}',
                },
            }
        ]
        if conditional
        else ["Done"]
    )
    _provider(monkeypatch, agent, replies)
    assert _speech(await _say(hass, agent, "Text only")) == "Done"
    assert calls == []


@pytest.mark.parametrize("destination", ["personal", "shared"])
async def test_short_term_creation_from_legacy_page_uses_destination(
    hass, hass_ws_client, destination
):
    from tests_real_ha.test_management_backend_acceptance import (
        ADMIN_ID,
        _admin_client,
        _management_call,
        _management_response,
    )

    from datetime import timedelta
    from homeassistant.util import dt as dt_util

    agent = await _agent(hass)
    client = await _admin_client(hass, hass_ws_client)
    owner = f"user:{ADMIN_ID}" if destination == "personal" else "shared:household"
    created = await _management_call(
        client,
        entry=agent.entry,
        section="memories",
        action="temporary_add",
        scope_id="__anonymous__",
        target_scope_id=owner,
        content="Created from legacy page",
        category="home",
        expires_at=(dt_util.utcnow() + timedelta(days=1)).isoformat(),
    )
    assert created["scope_id"] == owner
    listed = await _management_call(
        client,
        entry=agent.entry,
        section="memories",
        action="temporary_list",
        scope_id=owner,
    )
    assert any(
        record["content"] == "Created from legacy page"
        and record["owner_scope_id"] == owner
        for record in listed["memories"]
    )
    legacy = await _management_response(
        client,
        entry=agent.entry,
        section="memories",
        action="temporary_list",
        scope_id="__anonymous__",
        target_scope_id=owner,
    )
    assert legacy["success"] is False


@pytest.mark.parametrize("overlap", [False, True])
@pytest.mark.parametrize("mixed", [False, True])
async def test_native_responses_overlap_preserves_replay(hass, overlap, mixed):
    """Consume SDK native items with HA's production stream accumulator."""
    from tests.test_responses_api import (
        test_responses_overlapping_items_keep_native_replay,
    )

    await test_responses_overlapping_items_keep_native_replay(
        hass, overlap, mixed, True
    )


@pytest.mark.parametrize("overlap", [False, True])
async def test_native_responses_overlap_reaches_next_provider_request(hass, overlap):
    from tests.test_responses_api import test_responses_tool_chain_preserves_reasoning

    from custom_components.extended_openai_conversation_responses.completion_events import (
        completion_event_scope,
    )

    async with completion_event_scope():
        await test_responses_tool_chain_preserves_reasoning(hass, "low", overlap)


async def test_native_completed_responses_keep_indexed_item_text(hass):
    from tests.test_responses_api import (
        test_completed_responses_multiple_messages_preserve_indexed_text,
    )

    await test_completed_responses_multiple_messages_preserve_indexed_text(hass)


@pytest.mark.parametrize(
    "selection",
    [
        "area",
        "label",
        "control",
        "child_override",
        "entity_override",
        "legacy_read",
        "legacy_control",
    ],
)
async def test_guest_permissions_follow_real_child_device_area(hass, selection):
    from homeassistant.helpers import (
        area_registry as ar,
        device_registry as dr,
        entity_registry as er,
        label_registry as lr,
    )
    from custom_components.extended_openai_conversation_responses.guest_mode import (
        resolve_guest_policy,
        _resolve_legacy_entity_ids,
    )
    from custom_components.extended_openai_conversation_responses.const import (
        GUEST_POLICY_VERSION,
    )
    from types import SimpleNamespace

    agent = await _agent(hass)
    areas = ar.async_get(hass)
    bedroom = areas.async_create("Inherited bedroom")
    hall = areas.async_create("Override hall")
    label = lr.async_get(hass).async_create("Private inherited area")
    areas.async_update(bedroom.id, labels={label.label_id})
    devices = dr.async_get(hass)
    parent = devices.async_get_or_create(
        config_entry_id=agent.entry.entry_id,
        identifiers={(DOMAIN, "guest-area-parent")},
        name="Parent",
    )
    devices.async_update_device(parent.id, area_id=bedroom.id)
    child = devices.async_get_or_create_child(
        config_entry_id=agent.entry.entry_id,
        identifiers={(DOMAIN, "guest-area-child")},
        name="Child",
        parent_device_id=parent.id,
    )
    child = devices.async_update_child_device(
        child.id, area_id=hall.id if selection == "child_override" else None
    )
    entity = er.async_get(hass).async_get_or_create(
        "light",
        DOMAIN,
        "guest-area-light",
        device_id=child.id,
        config_entry=agent.entry,
        original_name="Inherited light",
        suggested_object_id="inherited_guest_light",
    )
    if selection == "entity_override":
        er.async_get(hass).async_update_entity(entity.entity_id, area_id=hall.id)
    hass.states.async_set(entity.entity_id, "on", {"friendly_name": "Inherited light"})
    async_expose_entity(hass, "conversation", entity.entity_id, True)
    assert dr.async_get_effective_area_id(hass, child) == (
        hall.id if selection == "child_override" else bedroom.id
    )
    if selection.startswith("legacy"):
        control = selection == "legacy_control"
        key = "guest_controllable_areas" if control else "guest_readable_areas"
        assert entity.entity_id in _resolve_legacy_entity_ids(
            hass, {key: [bedroom.id]}, control=control
        )
        return
    settings = {
        "guest_policy_version": GUEST_POLICY_VERSION,
        "guest_separate_control_restrictions": True,
    }
    key = (
        "guest_excluded_labels"
        if selection == "label"
        else "guest_control_excluded_areas"
        if selection == "control"
        else "guest_excluded_areas"
    )
    settings[key] = [label.label_id if selection == "label" else bedroom.id]
    policy = resolve_guest_policy(
        hass, settings, SimpleNamespace(is_active=lambda: True)
    )
    expected = (
        (True, True)
        if selection.endswith("override")
        else (True, False)
        if selection == "control"
        else (False, False)
    )
    assert (
        policy.allows_entity_read(entity.entity_id),
        policy.allows_entity_control(entity.entity_id),
    ) == expected


@pytest.mark.parametrize("change", ["retention", "title", "live_tokens"])
async def test_repair_save_applies_real_runtime_updates_without_conversation(
    hass, hass_ws_client, change
):
    import yaml
    from homeassistant.components import conversation
    from homeassistant.helpers import device_registry as dr
    from custom_components.extended_openai_conversation_responses.usage import (
        RequestUsage,
    )
    from tests_real_ha.test_management_backend_acceptance import (
        _admin_client,
        _management_call,
    )

    valid = {
        "spec": {
            "name": "healthy_probe",
            "description": "Probe",
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {"type": "template", "value_template": "OK"},
    }
    from copy import deepcopy

    broken = deepcopy(valid)
    broken["spec"]["name"] = "quarantined_probe"
    broken["spec"]["parameters"]["description"] = 123
    raw = yaml.safe_dump([valid, broken], sort_keys=False)
    agent = await _agent(
        hass,
        **{
            CONF_FUNCTION_TOOLS: raw,
            "usage_request_retention_days": 30,
            "usage_run_retention_days": 90,
        },
    )
    if change == "retention":
        async with agent._usage.async_run():
            await agent._usage.async_record_request(
                successful=True,
                usage=RequestUsage(input_tokens=2, output_tokens=1, total_tokens=3),
            )
        await hass.async_block_till_done(wait_background_tasks=True)
        assert agent._usage.requests and agent._usage.runs
    client = await _admin_client(hass, hass_ws_client)
    before = await _management_call(
        client, entry=agent.entry, section="configuration", action="get"
    )
    assert before.get("function_repair")
    changes = (
        {"config": {"usage_request_retention_days": 0, "usage_run_retention_days": 0}}
        if change == "retention"
        else {"title": "Renamed quarantined assistant"}
        if change == "title"
        else {"config": {"max_tokens": 777}}
    )
    saved = await _management_call(
        client,
        entry=agent.entry,
        section="function_repair",
        action="configuration_save",
        revision=before["revision"],
        **changes,
    )
    assert saved["valid"] is True
    await hass.async_block_till_done(wait_background_tasks=True)
    current = conversation.async_get_agent(hass, agent.entry.entry_id)
    assert current is not None
    assert current.subentry.data[CONF_FUNCTION_TOOLS] == raw
    if change == "live_tokens":
        assert current is agent
        assert current.subentry.data["max_tokens"] == 777
    else:
        assert current is not agent
    if change == "retention":
        assert (
            current._usage.request_retention_days
            == current._usage.run_retention_days
            == 0
        )
        assert current._usage.requests == current._usage.runs == []
        stored = await current._usage._detail_storage.async_load()
        assert stored["requests"] == stored["runs"] == []
    if change == "title":
        assert current.device_info["name"] == changes["title"]
        device = dr.async_get(hass).async_get_device_by_identifier(
            next(iter(current.device_info["identifiers"])), current.entry.entry_id
        )
        assert device is not None and device.name == changes["title"]
from tests_stress.test_os_storage_faults import real_store_io  # noqa: E402, F401


async def test_registered_catalogue_null_recovered_groups(hass, hass_ws_client):
    from custom_components.extended_openai_conversation_responses import backup
    from tests.test_management_request_boundaries import _tool
    from tests_real_ha.test_management_backend_acceptance import (
        _admin_client,
        _management_call,
    )
    from tests_real_ha.test_identity_isolation_matrix import _catalog

    broken = _tool("legacy_phone")
    broken["spec"]["parameters"]["properties"] = {
        "phone": {"type": "string", "minLength": "legacy"}
    }
    recovered = backup.recoverable_configuration_snapshot(
        {"functions": [broken], "function_groups": None}
    )
    assert recovered["function_groups"] is None
    agent = await _agent(hass, title="Recovered null Groups", **recovered)
    healthy = await _agent(hass, title="Healthy catalogue sibling")
    client = await _admin_client(hass, hass_ws_client)
    result = await _catalog(client)
    assert {row["entry_id"] for row in result} == {
        agent.entry.entry_id,
        healthy.entry.entry_id,
    }
    row = next(row for row in result if row["entry_id"] == agent.entry.entry_id)
    assert row["function_group_count"] == 0
    payload = await _management_call(
        client, entry=agent.entry, section="configuration", action="get"
    )
    assert payload["function_repair"]["invalid_count"] == 1
    assert agent.subentry.data["function_groups"] is None


@pytest.mark.parametrize("kind", ["tools", "groups"])
@pytest.mark.parametrize("enabled", [False, True])
async def test_registered_toggles_preserve_group_only_quarantine(hass, kind, enabled):
    from copy import deepcopy
    import yaml
    from homeassistant.core import Context
    from tests.test_management_request_boundaries import _tool

    healthy = _tool()
    healthy["enabled"] = not enabled
    group = {
        "id": "healthy-group",
        "name": "Healthy",
        "description": "Healthy",
        "loading_mode": "always",
        "enabled": not enabled,
        "functions": ["healthy"],
    }
    damaged = {
        **deepcopy(group),
        "id": "damaged",
        "name": "Damaged",
        "enabled": False,
        "functions": ["removed-old-tool"],
    }
    agent = await _agent(
        hass, functions=yaml.safe_dump([healthy]), function_groups=[group, damaged]
    )
    await hass.services.async_call(
        DOMAIN,
        ("enable_" if enabled else "disable_") + "function_" + kind,
        {
            "config_entry": agent.entry.entry_id,
            "agent_id": agent.entity_id,
            "functions" if kind == "tools" else "function_groups": [
                "healthy" if kind == "tools" else "healthy-group"
            ],
        },
        blocking=True,
        context=Context(),
    )
    assert agent.subentry.data["function_groups"][1] == damaged
    if kind == "tools":
        assert yaml.safe_load(agent.subentry.data["functions"])[0]["enabled"] is enabled
    else:
        assert agent.subentry.data["function_groups"][0]["enabled"] is enabled


async def test_cold_runtime_and_migration_yaml_are_off_loop(hass, monkeypatch):
    import threading
    import yaml
    from custom_components.extended_openai_conversation_responses import (
        agent_config,
        async_migrate_integration,
    )
    from custom_components.extended_openai_conversation_responses.management_function_repair import (
        async_prewarm_persisted_config_projection,
    )
    from tests_real_ha.test_cross_feature_acceptance import _say, _speech

    tools = []
    for index in range(36):
        tools.append(
            {
                "spec": {
                    "name": f"cold_runtime_{index}",
                    "description": "Read history",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            f"field_{i}": {
                                "type": "string",
                                "description": "Detailed schema " * 8,
                            }
                            for i in range(25)
                        },
                    },
                },
                "function": {"type": "native", "name": "get_history"},
            }
        )
    raw = yaml.safe_dump(tools)
    main, threads = threading.get_ident(), []
    original = yaml.safe_load

    def decode(value):
        if value == raw:
            threads.append(threading.get_ident())
        return original(value)

    monkeypatch.setattr(yaml, "safe_load", decode)
    agent_config._cached_configured_tools.cache_clear()
    agent_config._decoded_function_tools.cache_clear()
    agent = await _agent(hass, functions=raw, max_function_calls_per_conversation=0)
    await _agent(hass, title="Runtime sibling")
    await async_prewarm_persisted_config_projection(hass, agent.entry, agent.subentry)
    assert threads and main not in threads
    before = len(threads)
    await async_migrate_integration(hass)
    assert len(threads) > before and main not in threads
    assert agent.subentry.data["functions"] == raw
    sent = _provider(monkeypatch, agent, ["First answer", "Second answer"])
    assert _speech(await _say(hass, agent, "First")) == "First answer"
    first = len(threads)
    assert _speech(await _say(hass, agent, "Second")) == "Second answer"
    assert len(threads) == first and main not in threads
    assert len(sent) == 2


@pytest.mark.parametrize("count", [10, 1000])
async def test_native_memory_cold_preparation_worker(
    hass, monkeypatch, real_store_io, count
):
    import threading
    from custom_components.extended_openai_conversation_responses import memory
    from tests.test_cold_state_boundaries import records

    raw = records(count)
    entry_id, subentry_id = "cold-memory-provider", "cold-memory-agent"
    storage = memory.HomeAssistantMemoryStorage(hass, entry_id, subentry_id)
    cache = memory.HomeAssistantEmbeddingCacheStorage(hass, entry_id, subentry_id)
    await storage.async_save({"memories": [*raw, raw[0], {"bad": True}]})
    await cache.async_save(
        {
            "embeddings": {
                r["memory_id"]: {
                    "model": "default",
                    "fingerprint": "saved",
                    "vector": [0.5] * 256,
                }
                for r in raw
            }
        }
    )
    main, validations, vectors = threading.get_ident(), [], []
    validate, clean = memory._validate_persistent_memory_record, memory._clean_embedding

    def check(value):
        validations.append(threading.get_ident())
        return validate(value)

    def vector(value):
        vectors.append(threading.get_ident())
        return clean(value)

    monkeypatch.setattr(memory, "_validate_persistent_memory_record", check)
    monkeypatch.setattr(memory, "_clean_embedding", vector)
    manager = await memory.async_get_memory(hass, entry_id, subentry_id)
    assert len(manager._memories) == count
    assert len(manager._key_index) == count
    assert len(manager._embedding_cache) == count
    assert validations and main not in validations
    assert len(vectors) == count and main not in vectors
    saved = await storage.async_load()
    assert len(saved["memories"]) == count
    assert await memory.async_get_memory(hass, entry_id, subentry_id) is manager
    assert len(vectors) == count
