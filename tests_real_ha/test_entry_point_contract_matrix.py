"""Cross-entry-point guarantees; only transport/delivery is replaced."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.extended_openai_conversation_responses import (
    intercom_panel,
    intercom_services,
    local_intents,
)
from custom_components.extended_openai_conversation_responses.agent_config import (
    normalize_agent_config,
)
from custom_components.extended_openai_conversation_responses.function_execution import (
    propagate_function_execution_errors,
)
from custom_components.extended_openai_conversation_responses.functions import native
from custom_components.extended_openai_conversation_responses.functions.script import (
    ScriptFunction,
)
from custom_components.extended_openai_conversation_responses.ha_permissions import (
    bind_active_ha_context,
)
from custom_components.extended_openai_conversation_responses.management_ui import (
    async_management_command,
)
from homeassistant.components import conversation
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError
from tests_real_ha.test_cross_feature_acceptance import _agent, _rule
from tests_real_ha.test_provider_wire_e2e import _install_wire
from tests_real_ha.test_service_registry_acceptance import _response_service_call
from tests_real_ha.test_user_permission_acceptance import (
    _ALLOWED_ENTITY,
    _DENIED_ENTITY,
    _restricted_user,
)


async def _contract_agent(hass, **options):
    return await _agent(
        hass,
        **normalize_agent_config(
            {
                "chat_model": "gpt-5.6",
                "api_mode": "chat_completions",
                "reasoning_effort": "none",
                "functions": [],
                **options,
            }
        ),
    )


WRITERS = ("save", "update", "import_preview", "import_current", "import_new")
INVALID_REQUESTS = (
    pytest.param(
        {"api_mode": "chat_completions", "web_search": True}, id="hosted-search-api"
    ),
    pytest.param({"max_tokens": 9999999}, id="output-ceiling"),
    pytest.param(
        {"chat_model": "gpt-5-pro", "api_mode": "chat_completions"}, id="model-api"
    ),
)


async def _management(hass, agent):
    owner = await hass.auth.async_create_user(
        "Contract owner", group_ids=["system-admin"]
    )

    async def command(action, **values):
        return await async_management_command(
            hass,
            owner.id,
            True,
            {
                "section": "configuration",
                "entry_id": agent.entry.entry_id,
                "subentry_id": agent.subentry.subentry_id,
                "action": action,
                **values,
            },
        )

    return command


async def _write(command, route, changes, revision):
    if route in ("save", "update"):
        return await command(route, config=changes, revision=revision)
    document = (await command("export"))["document"]
    document["config"].update(changes)
    if route == "import_preview":
        return await command(route, document=document)
    return await command(
        "import",
        document=document,
        confirm=True,
        revision=revision,
        mode="new" if route == "import_new" else "current",
    )


@pytest.mark.parametrize("route", WRITERS)
@pytest.mark.parametrize("changes", INVALID_REQUESTS)
async def test_invalid_request_cannot_publish_through_any_setup_writer(
    hass, route, changes
):
    agent = await _contract_agent(hass)
    command = await _management(hass, agent)
    before = await command("get")
    entry = agent.entry
    original = {
        key: (item.title, deepcopy(dict(item.data)))
        for key, item in entry.subentries.items()
    }
    client = entry.runtime_data
    try:
        if route == "save":
            rejected = await _write(command, route, changes, before["revision"])
            assert rejected["valid"] is False
        else:
            with pytest.raises(HomeAssistantError):
                await _write(command, route, changes, before["revision"])
    finally:
        # A deliberately broken writer can start a reload before rejection.
        # Finish its tasks even when an assertion fails (mutation sensitivity).
        await hass.async_block_till_done()
    assert {
        key: (item.title, dict(item.data)) for key, item in entry.subentries.items()
    } == original
    assert (await command("get"))["revision"] == before["revision"]
    assert entry.runtime_data is client
    assert conversation.async_get_agent(hass, entry.entry_id) is agent


@pytest.mark.parametrize("route", ["save", "update"])
@pytest.mark.parametrize(
    "changes,reloads", [({"max_tokens": 777}, False), ({"chat_model": "gpt-4o"}, True)]
)
async def test_setup_writers_agree_on_live_and_reload_boundaries(
    hass, route, changes, reloads
):
    agent = await _contract_agent(hass)
    command = await _management(hass, agent)
    before = await command("get")
    await _write(command, route, changes, before["revision"])
    await hass.async_block_till_done()
    current = conversation.async_get_agent(hass, agent.entry.entry_id)
    assert (current is not agent) is reloads
    for key, value in changes.items():
        assert current.subentry.data[key] == value
    if reloads:
        assert current.device_info["model"] == changes["chat_model"]


@pytest.mark.parametrize("route", ["assist", "process"])
@pytest.mark.parametrize("failure", ["provider", "rule", "intent"])
async def test_failure_semantics_survive_every_conversation_doorway(
    hass, monkeypatch, route, failure
):
    agent = await _agent(hass, local_intents_enabled=failure == "intent")
    if failure == "provider":
        wire = _install_wire(
            monkeypatch,
            agent,
            [
                (
                    400,
                    {
                        "error": {
                            "message": "contract failure",
                            "type": "invalid_request_error",
                            "code": "contract_failure",
                        }
                    },
                )
            ],
        )
    elif failure == "rule":

        async def fail(call):
            raise HomeAssistantError("contract failure")

        hass.services.async_register("audit_contract", "fail", fail)
        await agent._request_rules.async_create(
            _rule(
                "local_action",
                {
                    "actions": [{"action": "audit_contract.fail"}],
                    "success_response": "Unexpected success",
                    "failure_response": "contract failure",
                },
                phrase="contract request",
            )
        )
    else:

        async def fail(*args, **kwargs):
            raise HomeAssistantError("contract failure")

        monkeypatch.setattr(local_intents, "async_try_handle_local_intent", fail)
        # Conversation imports the dispatcher directly.
        from custom_components.extended_openai_conversation_responses import (
            conversation as owner,
        )

        monkeypatch.setattr(owner, "async_try_handle_local_intent", fail)
    if route == "assist":
        result = await conversation.async_converse(
            hass=hass,
            text="contract request",
            conversation_id=None,
            context=Context(),
            language="en",
            agent_id=agent.entry.entry_id,
        )
        assert result.response.error_code is not None
        assert result.continue_conversation is False
    else:
        result = await _response_service_call(
            hass, "process", {"text": "contract request", "agent_id": agent.entity_id}
        )
        assert result["successful"] is False
        assert result["error_code"]
        assert result["error_message"]
        assert result["intent_response"]["response_type"] == "error"
        assert result["continue_conversation"] is False
    assert agent._usage.runs[-1].successful is False
    if failure == "provider":
        assert len(wire.requests) == 1


@pytest.mark.parametrize("route", ["native", "script", "nested_script", "request_rule"])
@pytest.mark.parametrize("allowed", [False, True])
async def test_action_routes_share_actual_user_control_boundary(hass, route, allowed):
    user = _restricted_user()
    user.add_to_hass(hass)
    entity_id = _ALLOWED_ENTITY if allowed else _DENIED_ENTITY
    hass.states.async_set(entity_id, "on")
    async_expose_entity(hass, "conversation", entity_id, True)
    effects = []

    async def turn_off(call):
        effects.append(call)
        hass.states.async_set(entity_id, "off")

    hass.services.async_register("light", "turn_off", turn_off)
    context = Context(user_id=user.id)
    exposed = [{"entity_id": entity_id}]

    async def execute():
        if route == "request_rule":
            agent = await _contract_agent(hass)
            await agent._request_rules.async_create(_rule("local_action", {
                "actions": [{"action": "light.turn_off", "target": {"entity_id": entity_id}}],
                "success_response": "Done", "failure_response": "Denied",
            }, phrase="contract control"))
            result = await conversation.async_converse(
                hass=hass, text="contract control", conversation_id=None,
                context=context, language="en", agent_id=agent.entry.entry_id,
            )
            assert len(effects) == (1 if allowed else 0), result.response.as_dict()
            if not allowed:
                assert "Denied" in str(result.response.as_dict())
                raise HomeAssistantError("Request Rule denied control")
            return result
        with bind_active_ha_context(context), propagate_function_execution_errors():
            if route == "native":
                return await native.NativeFunction().execute_service_single(
                    hass,
                    {},
                    {
                        "domain": "light",
                        "service": "turn_off",
                        "service_data": {"entity_id": entity_id},
                    },
                    SimpleNamespace(context=context),
                    exposed,
                )
            sequence = [
                {"action": "light.turn_off", "target": {"entity_id": "{{ target }}"}}
            ]
            if route == "nested_script":
                sequence = [
                    {
                        "repeat": {
                            "count": 1,
                            "sequence": [
                                {
                                    "choose": [
                                        {
                                            "conditions": "{{ true }}",
                                            "sequence": sequence,
                                        }
                                    ]
                                }
                            ],
                        }
                    }
                ]
            function = ScriptFunction()
            config = function.validate_schema({"type": "script", "sequence": sequence})
            return await function.execute(
                hass,
                config,
                {"target": entity_id},
                SimpleNamespace(context=context),
                exposed,
            )

    if allowed:
        await execute()
        assert len(effects) == 1
        assert effects[0].context.user_id == user.id
        assert hass.states.get(entity_id).state == "off"
    else:
        with pytest.raises(HomeAssistantError):
            await execute()
        assert effects == []
        assert hass.states.get(entity_id).state == "on"


@pytest.mark.parametrize("route", ["service", "websocket", "model", "local"])
@pytest.mark.parametrize("allowed", [False, True])
async def test_broadcast_routes_cannot_queue_denied_targets(
    hass, monkeypatch, route, allowed
):
    user = _restricted_user()
    user.add_to_hass(hass)
    target = _ALLOWED_ENTITY if allowed else _DENIED_ENTITY
    context = Context(user_id=user.id)
    # Replace delivery/discovery only; use HA's real user policy for authorization.
    manager = SimpleNamespace(
        enabled=True,
        resolve_targets=lambda **kwargs: [target],
        resolve_named_target=lambda name: {"entity_id": target},
        async_send=AsyncMock(
            return_value={"id": "message", "targets": [target], "deliveries": []}
        ),
    )
    for module in (intercom_services, intercom_panel, local_intents, native):
        monkeypatch.setattr(
            module, "async_get_intercom", AsyncMock(return_value=manager)
        )
    monkeypatch.setattr(
        local_intents,
        "parse_targeted_broadcast",
        lambda *args: ({"entity_ids": [target]}, "Dinner"),
    )

    async def execute():
        if route == "service":
            await intercom_services.async_setup_intercom_services(hass)
            return await _response_service_call(
                hass,
                "broadcast",
                {"message": "Dinner", "entity_id": [target]},
                user_id=user.id,
            )
        if route == "websocket":
            connection = SimpleNamespace(
                user=user,
                context=lambda msg: context,
                send_result=Mock(),
                send_error=Mock(),
            )
            await intercom_panel.websocket_broadcast.__wrapped__(
                hass,
                connection,
                {
                    "id": 1,
                    "action": "send",
                    "message": "Dinner",
                    "entity_ids": [target],
                },
            )
            assert connection.send_error.called is not allowed
            return None
        if route == "local":
            return await local_intents._async_try_targeted_broadcast(
                hass,
                SimpleNamespace(
                    text="broadcast to kitchen Dinner",
                    context=context,
                    language="en",
                    satellite_id=None,
                    device_id=None,
                ),
            )
        with bind_active_ha_context(context):
            return await native.NativeFunction().send_broadcast(
                hass, {}, {"destination": "Kitchen", "message": "Dinner"}, None, []
            )

    if allowed or route == "websocket":
        await execute()
    else:
        with pytest.raises(HomeAssistantError):
            await execute()
    assert manager.async_send.await_count == int(allowed)
    if allowed:
        assert manager.async_send.await_args.kwargs["entity_ids"] == [target]


@pytest.mark.parametrize("advanced", [False, True])
@pytest.mark.parametrize("changes", INVALID_REQUESTS)
async def test_ai_task_final_flow_paths_share_invalid_request_contract(
    hass, advanced, changes
):
    from homeassistant.config_entries import SOURCE_USER
    from homeassistant.data_entry_flow import FlowResultType
    from tests_real_ha.test_config_flow import _make_entry, _setup_entry, _unload_entry

    entry = _make_entry()
    await _setup_entry(hass, entry)
    try:
        original = {key: dict(item.data) for key, item in entry.subentries.items()}
        flow = await hass.config_entries.subentries.async_init(
            (entry.entry_id, "ai_task_data"), context={"source": SOURCE_USER}
        )
        flow = await hass.config_entries.subentries.async_configure(
            flow["flow_id"],
            {
                "name": "Invalid task",
                "chat_model": "gpt-4o",
                "api_mode": "chat_completions",
                "max_tokens": 500,
                "web_search": False,
                "advanced_options": advanced,
                **changes,
            },
        )
        if advanced:
            assert flow["step_id"] == "advanced"
            flow = await hass.config_entries.subentries.async_configure(
                flow["flow_id"],
                {},
            )
        assert flow["type"] is FlowResultType.FORM
        assert flow["errors"] == {"base": "invalid_request"}
        assert flow["description_placeholders"]["reason"]
        assert {
            key: dict(item.data) for key, item in entry.subentries.items()
        } == original
    finally:
        await _unload_entry(hass, entry)


@pytest.mark.parametrize("settings", [{"chat_model": "gpt-4o"}, {"web_search": True}])
async def test_settings_projection_cannot_be_used_as_general_configuration_writer(
    hass, settings
):
    agent = await _contract_agent(hass)
    owner = await hass.auth.async_create_user(
        "Settings owner", group_ids=["system-admin"]
    )
    before = dict(agent.subentry.data)
    with pytest.raises(HomeAssistantError, match="Unknown settings"):
        await async_management_command(
            hass,
            owner.id,
            True,
            {
                "section": "settings",
                "action": "update",
                "entry_id": agent.entry.entry_id,
                "subentry_id": agent.subentry.subentry_id,
                "settings": settings,
            },
        )
    assert dict(agent.subentry.data) == before
