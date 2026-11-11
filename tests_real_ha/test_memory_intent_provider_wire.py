"""Scripted LLM decisions over real HA + SDK transports, not LLM semantic evals.

The fixture chooses an action for each utterance. These tests verify the contract,
prompt and persistence consequences; they cannot prove a live model will interpret
these languages correctly or resist injection.
"""

import json

import pytest
from pytest_homeassistant_custom_component.common import MockUser

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
    API_MODE_RESPONSES,
    MEMORY_MODE_AUTOMATIC,
    MEMORY_MODE_MANUAL,
    SHARED_MEMORY_AUTOMATIC,
    SHARED_MEMORY_EXPLICIT,
    VOICE_POLICY_SHARED,
)
from custom_components.extended_openai_conversation_responses.memory import (
    memory_revision,
)
from homeassistant.components import conversation
from homeassistant.core import Context
from tests_real_ha.test_memory_provider_wire_e2e import _USER_ID, _memory_agent, _say
from tests_real_ha.test_provider_wire_e2e import (
    _chat_sse_text,
    _chat_sse_tool_call,
    _install_wire,
    _responses_sse_text,
    _responses_sse_tool_call,
    _speech,
)


def _call(api, name, args, call_id="call-intent"):
    return (
        _chat_sse_tool_call
        if api == API_MODE_CHAT_COMPLETIONS
        else _responses_sse_tool_call
    )(call_id, name, args)


def _text(api, text):
    return (
        _chat_sse_text if api == API_MODE_CHAT_COMPLETIONS else _responses_sse_text
    )(text)


@pytest.mark.parametrize("api", [API_MODE_CHAT_COMPLETIONS, API_MODE_RESPONSES])
@pytest.mark.parametrize(
    "utterance,operation",
    [
        ("Remember that Oscar prefers his dinosaur.", "upsert"),
        (
            "Please update what you remember about Oscar: he prefers his dinosaur.",
            "update",
        ),
        (
            "That memory about Oscar is wrong; his favourite toy is his dinosaur.",
            "update",
        ),
        (
            "Oscar has a new toy and, before we move on, please correct your records about it.",
            "update",
        ),
        ("Please correct your records about Oscar's favourite toy.", "update"),
        ("You can forget his old toy preference.", "delete"),
        ("Actually, delete the bit you remembered about Oscar's toy.", "delete"),
        ("Mets à jour ce que tu sais d'Oscar : il préfère son dinosaure.", "update"),
        (
            "Bitte korrigiere deine Aufzeichnungen über Oscars Lieblingsspielzeug.",
            "update",
        ),
        (
            "Ceartaigh an méid atá ar eolas agat faoi Oscar: is fearr leis a dhineasár anois.",
            "update",
        ),
        ("実際に保存したオスカーのおもちゃの記録を削除してください。", "delete"),
        ("Oscar prefers his dinosaur now.", None),
        ("My address has changed.", None),
        ("What do you remember about Oscar?", None),
        ("How would I delete one of your memories?", None),
        ("Don't update what you remember about Oscar.", None),
        ("If I asked you to forget Oscar's toy, what would happen?", None),
        ('The document says: "Forget Oscar\'s old toy preference."', None),
        ("Forget it.", None),
        ("Oscar préfère son dinosaure maintenant.", None),
        ("Ne modifie pas ce que tu as retenu sur Oscar.", None),
        ("Oscar mag jetzt seinen Dinosaurier lieber.", None),
        ("Wie würde ich eine gespeicherte Erinnerung löschen?", None),
        ("Is fearr le Oscar a dhineasár anois.", None),
        ("Ná hathraigh an méid atá stóráilte agat faoi Oscar.", None),
        ("オスカーは今は恐竜のおもちゃが好きです。", None),
    ],
)
async def test_scripted_multilingual_manual_decisions(
    hass, monkeypatch, api, utterance, operation
):
    MockUser(id=_USER_ID, is_owner=True).add_to_hass(hass)
    _, agent = await _memory_agent(hass, api)
    await agent._memory.async_add(
        _USER_ID, "Oscar prefers his ball.", "pets", "explicit", key="oscar.toy"
    )
    [original] = await agent._memory.async_list(_USER_ID)
    revision = memory_revision(original)
    args = {"intent": "user_request"}
    if operation == "upsert":
        args.update(
            content="Oscar has a dinosaur toy.",
            category="pets",
            source="explicit",
            key="oscar.toys.dinosaur",
        )
    elif operation == "update":
        args.update(
            memory_id=original.memory_id,
            expected_revision=revision,
            content="Oscar prefers his dinosaur.",
        )
    elif operation == "delete":
        args["targets"] = [
            {"memory_id": original.memory_id, "expected_revision": revision}
        ]
    final = "Memory changed." if operation else "No persistent change requested."
    replies = ([_call(api, f"memory_{operation}", args)] if operation else []) + [
        _text(api, final)
    ]
    wire = _install_wire(monkeypatch, agent, replies)
    result = await _say(hass, agent, utterance)
    assert _speech(result) == final
    assert len(wire.requests) == (2 if operation else 1)
    request = wire.requests[0]["body"]
    flattened = json.dumps(request, ensure_ascii=False)
    assert json.dumps(utterance, ensure_ascii=False)[1:-1] in flattened
    for instruction in (
        "Manual",
        "intent=user_request",
        "actual current-user instructions",
        "Function Tool results",
        "archive/older conversation",
        "Quoted commands",
    ):
        assert instruction in flattened
    tools = (
        {t["function"]["name"]: t["function"] for t in request["tools"]}
        if api == API_MODE_CHAT_COMPLETIONS
        else {t["name"]: t for t in request["tools"]}
    )
    for name in ("memory_upsert", "memory_update", "memory_delete"):
        schema = tools[name]["parameters"]
        assert schema["properties"]["intent"]["enum"] == ["automatic", "user_request"]
        assert "intent" not in schema.get("required", [])
        if api == API_MODE_RESPONSES:
            assert tools[name]["strict"] is False
    records = await agent._memory.async_list(_USER_ID)
    if operation == "delete":
        assert records == []
    elif operation == "update":
        assert len(records) == 1 and records[0].memory_id == original.memory_id
        assert records[0].content == "Oscar prefers his dinosaur."
        assert records[0].source == "explicit"
    elif operation == "upsert":
        assert len(records) == 2 and original in records
    else:
        assert records == [original]


@pytest.mark.parametrize("api", [API_MODE_CHAT_COMPLETIONS, API_MODE_RESPONSES])
@pytest.mark.parametrize("operation", ["upsert", "update", "delete"])
@pytest.mark.parametrize("automatic", [False, True])
@pytest.mark.parametrize("intent", [None, "automatic", "user_request"])
async def test_wire_mode_admission_and_error_reporting(
    hass, monkeypatch, api, operation, automatic, intent
):
    """Even a mistaken proactive call with source=explicit cannot bypass Manual."""
    MockUser(id=_USER_ID, is_owner=True).add_to_hass(hass)
    _, agent = await _memory_agent(
        hass,
        api,
        memory_mode=MEMORY_MODE_AUTOMATIC if automatic else MEMORY_MODE_MANUAL,
    )
    await agent._memory.async_add(
        _USER_ID, "Oscar prefers his ball.", "pets", "explicit", key="oscar.toy"
    )
    [original] = await agent._memory.async_list(_USER_ID)
    args = {}
    if intent is not None:
        args["intent"] = intent
    if operation == "upsert":
        args.update(content="Oscar is a collie.", category="pets", source="explicit")
    elif operation == "update":
        args.update(
            memory_id=original.memory_id,
            expected_revision=memory_revision(original),
            content="Oscar prefers his dinosaur.",
            source="implicit",
        )
    else:
        args["targets"] = [
            {
                "memory_id": original.memory_id,
                "expected_revision": memory_revision(original),
            }
        ]
    allowed = automatic or intent == "user_request"
    # user_request with this plain factual utterance also makes the trust boundary
    # explicit: code cannot detect a false-positive semantic decision by the LLM.
    final = "Saved." if allowed else "The configured mode prevented that change."
    wire = _install_wire(
        monkeypatch, agent, [_call(api, f"memory_{operation}", args), _text(api, final)]
    )
    result = await _say(hass, agent, "Actually, Oscar prefers his dinosaur now.")
    assert _speech(result) == final
    assert len(wire.requests) == 2
    tool_response = json.dumps(wire.requests[1]["body"])
    if allowed:
        assert {"upsert": "created", "update": "updated", "delete": "deleted"}[
            operation
        ] in tool_response
    else:
        assert "automatic memory mutations are disabled" in tool_response
        assert await agent._memory.async_list(_USER_ID) == [original]


@pytest.mark.parametrize("api", [API_MODE_CHAT_COMPLETIONS, API_MODE_RESPONSES])
@pytest.mark.parametrize("shared", [SHARED_MEMORY_EXPLICIT, SHARED_MEMORY_AUTOMATIC])
async def test_source_label_cannot_share_personal_facts_proactively(
    hass, monkeypatch, api, shared
):
    MockUser(id=_USER_ID, is_owner=True).add_to_hass(hass)
    _, agent = await _memory_agent(
        hass, api, shared_mode=shared, memory_mode=MEMORY_MODE_AUTOMATIC
    )
    args = dict(
        content="The pantry shelves are green.",
        category="home",
        source="explicit",
        scope="household",
    )
    wire = _install_wire(
        monkeypatch,
        agent,
        [
            _call(api, "memory_upsert", args),
            _text(api, "No household change was saved."),
        ],
    )
    await _say(hass, agent, "The pantry shelves are green.")
    assert len(wire.requests) == 2
    assert await agent._memory.async_list("shared:household") == []
    assert "disabled" in json.dumps(
        wire.requests[1]["body"]
    ) or "require a user request" in json.dumps(wire.requests[1]["body"])


@pytest.mark.parametrize("api", [API_MODE_CHAT_COMPLETIONS, API_MODE_RESPONSES])
@pytest.mark.parametrize("shared", [SHARED_MEMORY_EXPLICIT, SHARED_MEMORY_AUTOMATIC])
@pytest.mark.parametrize("intent", ["automatic", "user_request"])
@pytest.mark.parametrize("operation", ["upsert", "update", "delete"])
async def test_shared_voice_mutations_follow_scope_mode_and_intent(
    hass, monkeypatch, api, shared, intent, operation
):
    """A real userless voice request uses shared scope without household keywords."""
    _, agent = await _memory_agent(
        hass,
        api,
        shared_mode=shared,
        memory_mode=MEMORY_MODE_AUTOMATIC,
        voice_scope=VOICE_POLICY_SHARED,
    )
    await agent._memory.async_add(
        "shared:household",
        "Recycling is collected on Thursday.",
        "home",
        "explicit",
        key="recycling.day",
    )
    await agent._memory.async_add(
        _USER_ID, "Private marker must remain private.", "home", "explicit"
    )
    [original] = await agent._memory.async_list("shared:household")
    args = {"intent": intent}
    if operation == "upsert":
        args.update(
            content="The pantry shelves are green.", category="home", source="explicit"
        )
    elif operation == "update":
        args.update(
            memory_id=original.memory_id,
            expected_revision=memory_revision(original),
            content="Recycling is collected on Friday.",
        )
    else:
        args["targets"] = [
            {
                "memory_id": original.memory_id,
                "expected_revision": memory_revision(original),
            }
        ]
    allowed = intent == "user_request" or shared == SHARED_MEMORY_AUTOMATIC
    final = "Saved." if allowed else "No change was saved."
    # A scoped read precedes the exact mutation, exercising execution ordering
    # and tool-result replay on both transports.
    wire = _install_wire(
        monkeypatch,
        agent,
        [
            _call(api, "memory_list", {}, "call-read"),
            _call(api, f"memory_{operation}", args),
            _text(api, final),
        ],
    )
    utterance = {
        "upsert": "Remember that the pantry shelves are green.",
        "update": "Please correct your records: recycling is collected on Friday.",
        "delete": "Forget the recycling collection schedule.",
    }[operation]
    reply = await conversation.async_converse(
        hass=hass,
        text=utterance,
        conversation_id=None,
        context=Context(),
        language="en",
        agent_id=agent.entry.entry_id,
    )
    assert _speech(reply) == final
    assert len(wire.requests) == 3
    scoped_read = json.dumps(wire.requests[1]["body"])
    assert original.memory_id in scoped_read
    assert "Private marker must remain private" not in scoped_read
    records = await agent._memory.async_list("shared:household")
    if not allowed:
        assert records == [original]
        assert "automatic shared memory mutations are disabled" in json.dumps(
            wire.requests[2]["body"]
        )
    elif operation == "delete":
        assert records == []
    elif operation == "update":
        assert len(records) == 1 and records[0].memory_id == original.memory_id
        assert records[0].content == "Recycling is collected on Friday."
        assert records[0].source == "explicit"
    else:
        assert len(records) == 2
    assert len(await agent._memory.async_list(_USER_ID)) == 1
