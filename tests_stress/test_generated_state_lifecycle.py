"""Generated valid configurations across HA management, SDK wire and restore."""

from __future__ import annotations

import json
from time import monotonic
from uuid import uuid4

import httpx
import pytest
from pytest_homeassistant_custom_component.common import MockUser

from custom_components.extended_openai_conversation_responses import (
    agent_config,
    backup,
)
from custom_components.extended_openai_conversation_responses.const import (
    GUEST_POLICY_VERSION,
)
from custom_components.extended_openai_conversation_responses.model_capabilities import (
    parameter_is_allowed,
)
from custom_components.extended_openai_conversation_responses.model_catalog import (
    model_metadata,
)
from homeassistant.components import conversation
from homeassistant.core import Context, HomeAssistant
from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry
from tests_real_ha.test_management_backend_acceptance import (
    _admin_client,
    _conversation_subentry,
    _fresh_reload,
    _management_call,
)
from tests_real_ha.test_provider_wire_e2e import (
    _chat_sse_text,
    _install_wire,
    _raw_client,
    _responses_sse_text,
)
from tests_stress.behaviour_oracles import (
    FOREIGN,
    OTHER,
    OWNER,
    PRIVATE,
    SHARED,
    TEMPORARY,
    assert_feature_results,
    expected_speech,
    feature_calls,
    provider_reply,
    seed_behaviour,
)
from tests_stress.conftest import record
from tests_stress.generated_valid_states import generate, normalized_state
from tests_stress.test_provider_protocol_acceptance import (
    _assert_valid_outgoing_history,
)


def _completed_payload(api: str, model: str) -> dict:
    if api == "responses":
        return {
            "id": "resp-coverage",
            "object": "response",
            "created_at": 1,
            "model": model,
            "status": "completed",
            "output": [
                {
                    "id": "msg-coverage",
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [
                        {
                            "type": "output_text",
                            "text": "Coverage reply",
                            "annotations": [],
                        }
                    ],
                }
            ],
        }
    return {
        "id": "chatcmpl-coverage",
        "object": "chat.completion",
        "created": 1,
        "model": model,
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": "Coverage reply"},
            }
        ],
    }


async def _converse(
    hass: HomeAssistant,
    entry,
    monkeypatch,
    api: str,
    model: str,
    *,
    context: Context | None = None,
    text: str | None = None,
    case: dict | None = None,
    config: dict | None = None,
    source_id: str | None = None,
    effects: list | None = None,
    device_id: str | None = None,
    new_session: bool = True,
):
    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert agent is not None
    calls = feature_calls(case, config, source_id) if case else []
    if case:
        replies = [
            provider_reply(
                api, model, model_metadata(model)["streaming"], name, arguments, index
            )
            for index, (name, arguments, _expected) in enumerate(calls)
        ]
        replies.append(provider_reply(api, model, model_metadata(model)["streaming"]))
    elif model_metadata(model)["streaming"]:
        reply = (
            _responses_sse_text("Coverage reply")
            if api == "responses"
            else _chat_sse_text("Coverage reply")
        )
        replies = [reply.replace(b"gpt-5.6", model.encode())]
    else:
        replies = [(200, _completed_payload(api, model))]
    before_effects = len(effects) if effects is not None else 0
    wire = _install_wire(monkeypatch, agent, replies)
    conversation_send = wire.send

    wire_failures = []

    async def checked_send(request: httpx.Request, *args, **kwargs):
        if request.url.path == "/v1/embeddings":
            body = json.loads(request.content)
            inputs = body["input"]
            if isinstance(inputs, str):
                inputs = [inputs]
            return httpx.Response(
                200,
                json={
                    "object": "list",
                    "model": body["model"],
                    "data": [
                        {"object": "embedding", "index": index, "embedding": [0.5] * 8}
                        for index, _ in enumerate(inputs)
                    ],
                    "usage": {
                        "prompt_tokens": len(inputs),
                        "total_tokens": len(inputs),
                    },
                },
                request=request,
            )
        assert request.url.path == ("/v1/responses" if api == "responses" else "/v1/chat/completions"), (api, model, dict(agent.subentry.data), request.url.path)
        body = json.loads(request.content)
        _assert_valid_outgoing_history(body, api)
        if case:
            names = {
                tool.get("name", (tool.get("function") or {}).get("name"))
                for tool in body.get("tools", [])
            }
            index = len(wire.requests)
            if (
                index < len(calls)
                and calls[index][0] == "load_function_groups"
                and "load_function_groups" not in names
            ):
                # An already-loaded group exposes its action directly on later turns.
                assert "coverage_marker" in names
                calls.pop(index)
                wire._replies.pop(index)
            if index < len(calls):
                assert calls[index][0] in names, (calls[index][0], names, case)
        return await conversation_send(request, *args, **kwargs)

    async def send(request: httpx.Request, *args, **kwargs):
        try:
            return await checked_send(request, *args, **kwargs)
        except Exception as err:
            wire_failures.append(repr(err))
            raise

    monkeypatch.setattr(_raw_client(agent)._client, "send", send)
    result = await conversation.async_converse(
        hass=hass,
        text=text
        or ("What is the calibration token?" if case else "Generated coverage prompt"),
        conversation_id=uuid4().hex if new_session and case else None,
        context=context or Context(),
        device_id=device_id,
        language="en",
        agent_id=entry.entry_id,
    )
    if wire_failures:
        raise AssertionError(wire_failures[0])
    assert result.response.error_code is None, (result.response.as_dict()["speech"],)
    assert result.response.as_dict()["speech"]["plain"]["speech"] == (
        expected_speech(config) if case else "Coverage reply"
    )
    assert len(wire.requests) == len(calls) + 1
    if case:
        assert_feature_results(wire.requests, calls, api)
        initial_history = wire.requests[0]["body"][
            "input" if api == "responses" else "messages"
        ]
        prompt = json.dumps(initial_history[0])
        assert FOREIGN not in prompt
        personal = (
            config["memory_mode"] != "off" and config["memory_auto_retrieve_limit"] > 0
        )
        assert (PRIVATE in prompt) is personal
        shared = (
            config["shared_memory_mode"] != "disabled"
            and config["memory_mode"] != "off"
            and config["memory_auto_retrieve_limit"] > 0
        )
        # A continuing session keeps its original automatic memory bundle.
        # Explicit household retrieval above proves live availability independently.
        if not shared:
            assert SHARED not in prompt
        assert (TEMPORARY in prompt) is (config["temporary_memory"] != "off")
        if effects is not None:
            expected = (
                [("coverage_probe", "record", {"marker": "generated-action"})]
                if case["function_tools"] == "direct"
                else []
            )
            assert effects[before_effects:] == expected
    return wire.requests[0] | {
        "behaviours": [name for name, _args, _expected in calls]
        + ["speech", "memory privacy", "temporary memory"]
    }


def _assert_wire(request: dict, normalized: dict, api: str) -> None:
    body = request["body"]
    model = normalized["chat_model"]
    assert request["path"] == (
        "/v1/responses" if api == "responses" else "/v1/chat/completions"
    )
    assert body["model"] == model
    assert body.get("stream", False) is model_metadata(model)["streaming"]
    assert "max_tokens" not in body
    assert "functions" not in body
    assert "function_call" not in body
    for field in ("temperature", "top_p"):
        if field in body:
            assert parameter_is_allowed(
                model, field, normalized.get("reasoning_effort")
            )
    if "service_tier" in body:
        assert body["service_tier"] in model_metadata(model)["service_tiers"]
    if normalized["web_search"]:
        assert api == "responses"
        assert any(
            tool.get("type", "").startswith("web_search")
            for tool in body.get("tools", [])
        )
    if normalized.get("reasoning_effort") is not None:
        sent_effort = (
            (body.get("reasoning") or {}).get("effort")
            if api == "responses"
            else body.get("reasoning_effort")
        )
        assert sent_effort == normalized["reasoning_effort"]


async def test_generated_valid_states_cross_real_ha_and_sdk_wire(
    hass: HomeAssistant,
    hass_ws_client,
    monkeypatch,
    stress_seed: int,
    pytestconfig,
    stress_trace: list[dict],
) -> None:
    started = monotonic()
    MockUser(id=OWNER, name="Generated owner", is_owner=True).add_to_hass(hass)
    MockUser(id=OTHER, name="Other generated user").add_to_hass(hass)
    effects = []

    async def marker(call):
        effects.append((call.domain, call.service, dict(call.data)))

    hass.services.async_register("coverage_probe", "record", marker)
    heavy = pytestconfig.getoption("--stress-intensity") == "heavy"
    suite = generate(stress_seed, heavy=heavy)
    entry = _make_entry(
        "Generated valid states",
        include_ai_task=False,
        conversation_options={
            "functions": [],
            "guest_policy_version": GUEST_POLICY_VERSION,
        },
    )
    await _setup_entry(hass, entry)
    client = await _admin_client(hass, hass_ws_client)
    source_id = await seed_behaviour(hass, entry, _conversation_subentry(entry))
    for number, case in enumerate(suite.cases):
        case_started = monotonic()
        case_effect_start = len(effects)
        normalized, api = normalized_state(case)
        record(
            stress_trace,
            "generated_state_start",
            case=number,
            model=normalized["chat_model"],
            api=api,
        )
        before = await _management_call(
            client, entry=entry, section="configuration", action="get"
        )
        await _management_call(
            client,
            entry=entry,
            section="configuration",
            action="update",
            revision=before["revision"],
            config=normalized,
        )
        # Exercise the saved runtime before reload can repair stale managers/config.
        request = await _converse(
            hass,
            entry,
            monkeypatch,
            api,
            normalized["chat_model"],
            context=Context(user_id=OWNER),
            case=case,
            config=normalized,
            source_id=source_id,
            effects=effects,
        )
        _assert_wire(request, normalized, api)
        await _fresh_reload(hass, entry)
        current = await _management_call(
            client, entry=entry, section="configuration", action="get"
        )
        persisted = agent_config.normalize_agent_config(current["config"])
        assert persisted == normalized
        request = await _converse(
            hass,
            entry,
            monkeypatch,
            api,
            normalized["chat_model"],
            context=Context(user_id=OWNER),
            case=case,
            config=normalized,
            source_id=source_id,
            effects=effects,
        )
        _assert_wire(request, normalized, api)
        # Backup is sampled across the covering sequence: every state crosses the
        # management/reload/wire boundary, while restore exercises distinct states.
        if number % max(1, len(suite.cases) // 12) == 0:
            subentry = _conversation_subentry(entry)
            snapshot = await backup.async_collect_backup_snapshot(hass, entry, subentry)
            mutated = normalized | {
                "advanced_options": not normalized["advanced_options"]
            }
            updated = await _management_call(
                client, entry=entry, section="configuration", action="get"
            )
            await _management_call(
                client,
                entry=entry,
                section="configuration",
                action="update",
                revision=updated["revision"],
                config=mutated,
            )
            restored = await backup.async_restore_backup(
                hass, entry, subentry, snapshot
            )
            assert restored["status"] == "restored"
            await hass.async_block_till_done()
            recovered = await _management_call(
                client, entry=entry, section="configuration", action="get"
            )
            assert (
                agent_config.normalize_agent_config(recovered["config"]) == normalized
            )
            request = await _converse(
                hass,
                entry,
                monkeypatch,
                api,
                normalized["chat_model"],
                context=Context(user_id=OWNER),
                case=case,
                config=normalized,
                source_id=source_id,
                effects=effects,
            )
            _assert_wire(request, normalized, api)
        record(
            stress_trace,
            "generated_state",
            case=number,
            model=normalized["chat_model"],
            api=api,
            behaviours_exercised=request["behaviours"],
            actual_tool_executions=len(effects) - case_effect_start,
            live_and_reloaded=True,
            elapsed_seconds=round(monotonic() - case_started, 3),
        )
    record(
        stress_trace,
        "generated_suite",
        **suite.evidence(stress_seed),
        elapsed_seconds=round(monotonic() - started, 3),
    )


@pytest.mark.parametrize("api", ["chat_completions", "responses"])
async def test_populated_feature_combinations_work_live_and_after_reload(
    hass, hass_ws_client, monkeypatch, api, stress_seed, stress_trace
):
    """Six bounded witnesses exercise data, groups, voice, continuity and Guest effects."""
    import random

    from custom_components.extended_openai_conversation_responses.guest_mode import (
        async_get_guest_mode,
    )
    from homeassistant.components.homeassistant.exposed_entities import (
        async_expose_entity,
    )
    from homeassistant.helpers import (
        area_registry as ar,
        device_registry as dr,
        entity_registry as er,
    )
    from tests_stress.behaviour_oracles import KNOWLEDGE, RAW_SPEECH
    from tests_stress.generated_valid_states import GROUP, TOOL

    MockUser(id=OWNER, name="Feature owner", is_owner=True).add_to_hass(hass)
    MockUser(id=OTHER, name="Other feature user").add_to_hass(hass)
    entry = _make_entry(
        "Populated generated features",
        include_ai_task=False,
        conversation_options={
            "chat_model": "gpt-5.6",
            "api_mode": api,
            "reasoning_effort": "none",
            "functions": [],
            "knowledge_enabled": False,
            "memory_mode": "off",
        },
    )
    await _setup_entry(hass, entry)
    client = await _admin_client(hass, hass_ws_client)
    subentry = _conversation_subentry(entry)
    source_id = await seed_behaviour(hass, entry, subentry)
    area = ar.async_get(hass).async_create("Kitchen")
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={("generated_features", "voice-source")},
        name="Kitchen voice device",
    )
    dr.async_get(hass).async_update_device(device.id, area_id=area.id)
    light = er.async_get(hass).async_get_or_create(
        "light", "generated_features", "kitchen", original_name="Kitchen lights"
    )
    er.async_get(hass).async_update_entity(light.entity_id, area_id=area.id)
    async_expose_entity(hass, conversation.DOMAIN, light.entity_id, True)
    effects = []

    async def marker(call):
        effects.append((call.domain, call.service, dict(call.data)))

    async def turn_off(call):
        effects.append((call.domain, call.service, dict(call.data)))
        hass.states.async_set(
            light.entity_id, "off", {"friendly_name": "Kitchen lights"}
        )

    hass.services.async_register("coverage_probe", "record", marker)
    hass.services.async_register("light", "turn_off", turn_off)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    await agent._request_rules.async_create(
        {
            "name": "Protected generated local command",
            "phrases": ["generated protected command"],
            "match_type": "equals",
            "action_type": "local_action",
            "action": {
                "actions": [
                    {
                        "domain": "light",
                        "service": "turn_off",
                        "target": {"entity_id": [light.entity_id]},
                        "data": {},
                    }
                ],
                "success_response": "Protected light turned off",
            },
        }
    )
    modes = ["none", "always", "on_demand"]
    random.Random(stress_seed).shuffle(modes)
    for mode in modes:
        config = {
            "chat_model": "gpt-5.6",
            "api_mode": api,
            "reasoning_effort": "none",
            "functions": [TOOL],
            "function_groups": []
            if mode == "none"
            else [{**GROUP, "loading_mode": mode}],
            "memory_mode": "manual",
            "memory_auto_retrieve_limit": 3,
            "memory_retrieval_mode": "lexical",
            "shared_memory_mode": "explicit",
            "temporary_memory": "balanced",
            "knowledge_enabled": True,
            "archive_enabled": True,
            "conversation_continuity": "device",
            "voice_scope_policy": "device_mapping",
            "voice_device_mappings": {device.id: OWNER},
            "local_intents_enabled": True,
            "exposed_entities_enabled": True,
            "speech_processing_enabled": True,
            "speech_strip_markdown": True,
            "speech_strip_urls": True,
            "speech_regex_replacements": [{"pattern": "alpha", "replacement": "beta"}],
            "guest_mode_enabled": True,
            "guest_policy_version": GUEST_POLICY_VERSION,
            "guest_function_policy": "off",
            "guest_knowledge_policy": "off",
            "guest_shared_memory_policy": "off",
            "guest_excluded_entities": [light.entity_id],
        }
        before = await _management_call(
            client, entry=entry, section="configuration", action="get"
        )
        await _management_call(
            client,
            entry=entry,
            section="configuration",
            action="update",
            revision=before["revision"],
            config=config,
        )
        case = {"function_tools": "direct", "function_groups": mode}
        for boundary in ["live save", "reload"]:
            boundary_effect_start = len(effects)
            if boundary == "reload":
                await _fresh_reload(hass, entry)
            first = await _converse(
                hass,
                entry,
                monkeypatch,
                api,
                "gpt-5.6",
                context=Context(),
                device_id=device.id,
                case=case,
                config=config,
                source_id=source_id,
                effects=effects,
                new_session=False,
            )
            assert PRIVATE in json.dumps(first["body"])
            second = await _converse(
                hass,
                entry,
                monkeypatch,
                api,
                "gpt-5.6",
                context=Context(),
                device_id=device.id,
                case=case,
                config=config,
                source_id=source_id,
                effects=effects,
                new_session=False,
            )
            history = json.dumps(
                second["body"]["input" if api == "responses" else "messages"]
            )
            assert (
                RAW_SPEECH in history
            )  # Exact previous provider text survives speech cleanup.
            agent = conversation.async_get_agent(hass, entry.entry_id)
            wire = _install_wire(monkeypatch, agent, [])
            hass.states.async_set(
                light.entity_id, "on", {"friendly_name": "Kitchen lights"}
            )
            baseline = len(effects)
            local = await conversation.async_converse(
                hass=hass,
                text="turn off the lights",
                conversation_id=None,
                context=Context(),
                language="en",
                agent_id=entry.entry_id,
                device_id=device.id,
            )
            assert local.response.error_code is None
            assert effects[baseline:] == [
                ("light", "turn_off", {"entity_id": [light.entity_id]})
            ]
            assert wire.requests == []
            guest = await async_get_guest_mode(
                hass, entry.entry_id, subentry.subentry_id
            )
            await guest.async_update_trusted(indefinite=True)
            baseline = len(effects)
            denied = await conversation.async_converse(
                hass=hass,
                text="generated protected command",
                conversation_id=None,
                context=Context(),
                language="en",
                agent_id=entry.entry_id,
                device_id=device.id,
            )
            assert (
                denied.response.as_dict()["speech"]["plain"]["speech"]
                == "This capability is unavailable in Guest Mode."
            )
            assert effects[baseline:] == []
            assert wire.requests == []
            guest_reply = (
                _responses_sse_text(RAW_SPEECH)
                if api == "responses"
                else _chat_sse_text(RAW_SPEECH)
            )
            wire = _install_wire(monkeypatch, agent, [guest_reply])
            result = await conversation.async_converse(
                hass=hass,
                text="What is the calibration token?",
                conversation_id=None,
                context=Context(),
                language="en",
                agent_id=entry.entry_id,
                device_id=device.id,
            )
            assert result.response.error_code is None
            assert (
                result.response.as_dict()["speech"]["plain"]["speech"]
                == "beta visit done."
            )
            payload = json.dumps(wire.requests[0]["body"])
            for marker_text in [PRIVATE, SHARED, FOREIGN, TEMPORARY, KNOWLEDGE]:
                assert marker_text not in payload
            tools = wire.requests[0]["body"].get("tools", [])
            names = {
                tool.get("name", (tool.get("function") or {}).get("name"))
                for tool in tools
            }
            assert not names.intersection(
                {"coverage_marker", "memory_search", "knowledge_search"}
            )
            assert effects[baseline:] == []
            await guest.async_disable_trusted()
            record(
                stress_trace,
                "populated_behaviour",
                api=api,
                group_loading=mode,
                boundary=boundary,
                exact_action_effects=True,
                actual_tool_executions=sum(
                    domain == "coverage_probe"
                    for domain, _service, _data in effects[boundary_effect_start:]
                ),
                ha_service_effects=len(effects) - boundary_effect_start,
                voice_owner=OWNER,
                guest_restricted=True,
                local_provider_calls=0,
                continuity=True,
            )
