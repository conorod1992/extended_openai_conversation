"""Populated five-feature interactions through public Assist and real SDK dispatch."""

from copy import deepcopy
import json
import os
from pathlib import Path

import pytest
from pytest_homeassistant_custom_component.common import MockUser

from ci.enhanced_evidence import envelope, evidence_filename, write_json
from custom_components.extended_openai_conversation_responses.const import (
    CONF_API_MODE,
    CONF_ARCHIVE_ENABLED,
    CONF_FUNCTION_GROUPS,
    CONF_FUNCTION_TOOLS,
    CONF_GUEST_ALLOWED_GROUP_IDS,
    CONF_GUEST_FUNCTION_POLICY,
    CONF_GUEST_KNOWLEDGE_POLICY,
    CONF_GUEST_MODE_ENABLED,
    CONF_GUEST_POLICY_VERSION,
    CONF_GUEST_SHARED_MEMORY_POLICY,
    CONF_KNOWLEDGE_ENABLED,
    CONF_MEMORY_AUTO_RETRIEVE_LIMIT,
    CONF_MEMORY_MODE,
    CONF_SHARED_MEMORY_MODE,
    DEFAULT_CONF_FUNCTION_TOOLS,
    GUEST_POLICY_VERSION,
)
from custom_components.extended_openai_conversation_responses.conversation_archive import (
    async_get_archive,
)
from homeassistant.components import conversation
from homeassistant.components.assist_pipeline.pipeline import (
    PipelineEventType,
    PipelineInput,
    PipelineRun,
    PipelineStage,
    async_get_pipeline,
)
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.core import Context
from homeassistant.helpers import chat_session
from homeassistant.setup import async_setup_component
from tests_real_ha.test_cross_feature_acceptance import _agent
from tests_real_ha.test_knowledge_provider_wire_e2e import (
    _responses_sse_tool_call,
    _tool_names,
)
from tests_real_ha.test_provider_wire_e2e import (
    _chat_sse_text,
    _chat_sse_tool_call,
    _install_wire,
    _responses_sse_text,
)
from tests_stress.effect_ledger import assert_effect_ledger

_OWNER = "populated-assist-owner"
_FOREIGN = "populated-assist-foreign"
_TARGET = "light.populated_assist_fixture"
_GROUP = "fixture-control"
_PERSONAL = "private-cobalt"
_SHARED = "shared-copper"
_FOREIGN_MARKER = "foreign-amber"
_KNOWLEDGE = "The calibration handbook marker is handbook-saffron."
_QUERY = "What is my calibration token?"


async def _assist(hass, agent, conversation_id, text):
    """HA constructs the actual ConversationInput, listener and intent result."""
    events = []
    with chat_session.async_get_chat_session(hass, conversation_id) as session:
        pipeline_input = PipelineInput(
            intent_input=text,
            session=session,
            run=PipelineRun(
                hass,
                context=Context(user_id=_OWNER),
                pipeline=async_get_pipeline(hass, pipeline_id=agent.entity_id),
                start_stage=PipelineStage.INTENT,
                end_stage=PipelineStage.INTENT,
                event_callback=events.append,
            ),
        )
        await pipeline_input.validate()
        await pipeline_input.execute()
    assert events[0].type == PipelineEventType.RUN_START
    assert events[-1].type == PipelineEventType.RUN_END
    ends = [event for event in events if event.type == PipelineEventType.INTENT_END]
    assert len(ends) == 1
    result = ends[0].data["intent_output"]["response"]
    assert "error" not in result
    return result["speech"]["plain"]["speech"]


async def _archive_effects(archive):
    sessions = (await archive.async_list_sessions(f"user:{_OWNER}"))["sessions"]
    effects = []
    for session in sessions:
        detail = await archive.async_get(f"user:{_OWNER}", session["session_id"])
        effects.append(
            {
                "owner": session["scope_id"],
                "conversation": session["home_assistant_conversation_id"],
                "turn_count": session["turn_count"],
                "turns": [
                    (turn["user_text"], turn["assistant_text"], turn["successful"])
                    for turn in detail["turns"]
                ],
            }
        )
    return effects


def _tool_result(body, api_mode, call_id):
    if api_mode == "responses":
        item = next(
            item
            for item in body["input"]
            if item.get("type") == "function_call_output"
            and item.get("call_id") == call_id
        )
        outer = json.loads(item["output"])
    else:
        item = next(
            item
            for item in body["messages"]
            if item.get("role") == "tool" and item.get("tool_call_id") == call_id
        )
        outer = json.loads(item["content"])
    return (
        json.loads(outer["result"]) if isinstance(outer.get("result"), str) else outer
    )


def _expected(api_mode, phase):
    """Reviewed expected effects, independent of production generators/results."""
    return {
        "provider_paths": [
            "/v1/responses" if api_mode == "responses" else "/v1/chat/completions"
        ]
        * 4,
        "on_demand_visible": [False, True, True, True],
        "memory_write_visible": phase == "owner",
        "knowledge_in_initial_prompt": False,
        "restricted_markers_visible": False,
        "memory_markers": sorted([_PERSONAL, _SHARED])
        if phase == "owner"
        else [_SHARED],
        "knowledge": {"title": "Calibration handbook", "content": _KNOWLEDGE},
        "loaded_groups": [_GROUP],
        "tool_success": [True],
        "service_effects": [("light", "turn_off", [_TARGET], _OWNER)],
        "state": "off",
        "owner": _OWNER,
        "result": f"{phase.capitalize()} fixture ready.",
        "archive": [
            {
                "owner": f"user:{_OWNER}",
                "conversation": "populated-owner",
                "turn_count": 1,
                "turns": [(_QUERY, "Owner fixture ready.", True)],
            }
        ],
    }


@pytest.mark.parametrize("api_mode", ["chat_completions", "responses"])
async def test_populated_assist_feature_effects_and_real_oracle_canaries(
    hass, monkeypatch, api_mode, request
):
    """Owned data, on-demand execution, archive and guest restrictions interact."""
    MockUser(id=_OWNER, name="Populated Assist Owner", is_owner=True).add_to_hass(hass)
    MockUser(id=_FOREIGN, name="Other User").add_to_hass(hass)
    tool = deepcopy(DEFAULT_CONF_FUNCTION_TOOLS[0])
    assert tool["spec"]["name"] == "execute_services"
    agent = await _agent(
        hass,
        title="Populated Assist Features",
        **{
            CONF_API_MODE: api_mode,
            CONF_MEMORY_MODE: "manual",
            CONF_MEMORY_AUTO_RETRIEVE_LIMIT: 3,
            CONF_SHARED_MEMORY_MODE: "explicit",
            CONF_KNOWLEDGE_ENABLED: True,
            CONF_ARCHIVE_ENABLED: True,
            CONF_GUEST_MODE_ENABLED: True,
            CONF_GUEST_POLICY_VERSION: GUEST_POLICY_VERSION,
            CONF_GUEST_SHARED_MEMORY_POLICY: "read_only",
            CONF_GUEST_KNOWLEDGE_POLICY: "on",
            CONF_GUEST_FUNCTION_POLICY: "custom",
            CONF_GUEST_ALLOWED_GROUP_IDS: [_GROUP],
            CONF_FUNCTION_TOOLS: [tool],
            CONF_FUNCTION_GROUPS: [
                {
                    "id": _GROUP,
                    "name": "Fixture control",
                    "description": "Harmless fixture control",
                    "loading_mode": "on_demand",
                    "functions": ["execute_services"],
                    "enabled": True,
                }
            ],
        },
    )
    for owner, marker in [
        (_OWNER, _PERSONAL),
        ("shared:household", _SHARED),
        (_FOREIGN, _FOREIGN_MARKER),
    ]:
        await agent._memory.async_add(
            owner, f"The calibration token is {marker}.", "general", "explicit"
        )
    await agent._memory.async_add(
        _OWNER, "My favourite soup is minestrone.", "food", "explicit"
    )
    source = await agent._knowledge.async_create(
        "Calibration handbook", "Fixture calibration reference", _KNOWLEDGE
    )
    await agent._knowledge.async_create(
        "Unrelated garden notes",
        "Irrigation reference",
        "Unrelated marker is garden-violet.",
    )
    archive = await async_get_archive(
        hass, agent.entry.entry_id, agent.subentry.subentry_id
    )
    assert await async_setup_component(hass, "assist_pipeline", {})
    async_expose_entity(hass, conversation.DOMAIN, _TARGET, True)
    effects = []

    async def turn_off(call):
        effects.append(
            (
                call.domain,
                call.service,
                list(call.data["entity_id"]),
                call.context.user_id,
            )
        )
        hass.states.async_set(_TARGET, "off")

    hass.services.async_register("light", "turn_off", turn_off)
    call = _responses_sse_tool_call if api_mode == "responses" else _chat_sse_tool_call
    text_reply = _responses_sse_text if api_mode == "responses" else _chat_sse_text
    evidence = []
    rejections = 0
    for phase in ["owner", "guest"]:
        if phase == "guest":
            await agent._guest_mode.async_update_trusted(indefinite=True)
        hass.states.async_set(_TARGET, "on")
        effects.clear()
        wire = _install_wire(
            monkeypatch,
            agent,
            [
                call(f"{phase}-load", "load_function_groups", {"groups": [_GROUP]}),
                call(
                    f"{phase}-knowledge",
                    "knowledge_get",
                    {"source_id": source.source_id},
                ),
                call(
                    f"{phase}-effect",
                    "execute_services",
                    {
                        "list": [
                            {
                                "domain": "light",
                                "service": "turn_off",
                                "service_data": {"entity_id": [_TARGET]},
                            }
                        ]
                    },
                ),
                text_reply(f"{phase.capitalize()} fixture ready."),
            ],
        )
        spoken = await _assist(hass, agent, f"populated-{phase}", _QUERY)
        bodies = [request["body"] for request in wire.requests]
        assert len(bodies) == 4
        first = json.dumps(bodies[0], ensure_ascii=False)
        all_requests = json.dumps(bodies, ensure_ascii=False)
        restricted = [_FOREIGN_MARKER, "garden-violet"]
        if phase == "guest":
            restricted.append(_PERSONAL)
        knowledge = _tool_result(bodies[2], api_mode, f"{phase}-knowledge")
        tool_result = _tool_result(bodies[3], api_mode, f"{phase}-effect")
        actual = {
            "provider_paths": [request["path"] for request in wire.requests],
            "on_demand_visible": [
                "execute_services" in _tool_names(body, api_mode) for body in bodies
            ],
            "memory_write_visible": "memory_upsert" in _tool_names(bodies[0], api_mode),
            "knowledge_in_initial_prompt": _KNOWLEDGE in first,
            "restricted_markers_visible": any(
                marker in all_requests for marker in restricted
            ),
            "memory_markers": sorted(
                marker
                for marker in [_PERSONAL, _SHARED, _FOREIGN_MARKER]
                if marker in first
            ),
            "knowledge": {"title": knowledge["title"], "content": knowledge["content"]},
            "loaded_groups": _tool_result(bodies[1], api_mode, f"{phase}-load")[
                "loaded"
            ],
            "tool_success": [item["success"] for item in tool_result["result"]],
            "service_effects": deepcopy(effects),
            "state": hass.states.get(_TARGET).state,
            "owner": effects[0][3] if effects else None,
            "result": spoken,
            "archive": await _archive_effects(archive),
        }
        expected = _expected(api_mode, phase)
        assert_effect_ledger(actual, expected)
        assert (await archive.async_list_sessions(f"user:{_FOREIGN}"))["sessions"] == []
        # Mutate captured evidence from the real journey. These canaries use the
        # exact oracle above, rather than validating equality of fabricated data.
        mutations = []
        for field, value in [
            ("provider_paths", actual["provider_paths"][:-1]),
            ("service_effects", []),
            ("service_effects", actual["service_effects"] * 2),
            ("owner", _FOREIGN),
            ("result", "Incorrect result"),
            (
                "knowledge",
                {"title": "Calibration handbook", "content": "Wrong content"},
            ),
            ("archive", []),
        ]:
            mutation = deepcopy(actual)
            mutation[field] = value
            mutations.append(mutation)
        for mutation in mutations:
            with pytest.raises(AssertionError, match="Observed journey effects differ"):
                assert_effect_ledger(mutation, expected)
            rejections += 1
        evidence.append(
            {
                "operation": "semantic_effects",
                "phase": phase,
                "actual": actual,
                "expected": expected,
            }
        )
    if directory := os.environ.get("STRESS_ARTIFACT_DIR"):
        write_json(
            Path(directory) / evidence_filename(request.node.nodeid),
            envelope(
                test=request.node.nodeid,
                outcome="passed",
                operations=[
                    *evidence,
                    {
                        "operation": "summary",
                        "layer": "Real HA Assist and provider wire",
                        "populated_feature_journeys": len(evidence),
                        "semantic_oracle_rejections": rejections,
                    },
                ],
            ),
        )
