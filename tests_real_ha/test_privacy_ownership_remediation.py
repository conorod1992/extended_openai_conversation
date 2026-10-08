"""Owner, policy and retained-data privacy across real HA/provider requests."""

import json

from aiohttp import web
import pytest

from custom_components.extended_openai_conversation_responses.const import (
    CONF_FUNCTION_TOOLS,
)
from custom_components.extended_openai_conversation_responses.functions.web import (
    RestFunction,
)
from custom_components.extended_openai_conversation_responses.scope import (
    SHARED_HOUSEHOLD_SCOPE_ID,
)
from homeassistant.components import conversation
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError
from tests_real_ha.test_conversation_runtime_lifecycle import _tool
from tests_real_ha.test_cross_feature_acceptance import _agent, _provider, _say
from tests_real_ha.test_provider_wire_e2e import (
    _chat_sse_text,
    _chat_sse_tool_call,
    _install_wire,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("guest_active", [False, True])
@pytest.mark.parametrize("retrieval_mode", ["lexical", "hybrid"])
async def test_explicit_unretained_with_guest_shared_memory(
    hass, monkeypatch, guest_active, retrieval_mode
):
    agent = await _agent(
        hass,
        memory_retrieval_mode=retrieval_mode,
        memory_mode="manual",
        shared_memory_mode="explicit",
        memory_auto_retrieve_limit=5,
        guest_policy_version=2,
        guest_shared_memory_policy="read_only",
        voice_scope_policy="device_mapping",
        voice_unmapped_policy="shared_household",
        voice_device_mappings={"private-satellite": "unretained"},
    )
    await agent._memory.async_add(
        SHARED_HOUSEHOLD_SCOPE_ID,
        "Calibration shared marker: audit-shared-violet.",
        "calibration",
        "explicit",
    )
    if guest_active:
        await agent._guest_mode.async_update_trusted(indefinite=True)
    requests = _privacy_wire(monkeypatch, agent)
    result = await conversation.async_converse(
        hass=hass,
        text="Find calibration shared marker",
        conversation_id=None,
        context=Context(),
        language="en",
        agent_id=agent.entry.entry_id,
        device_id="private-satellite",
    )
    assert result.response.error_code is None
    assert (
        len(
            [
                request
                for request in requests
                if not request["path"].endswith("/embeddings")
            ]
        )
        == 1
    )
    assert "audit-shared-violet" not in json.dumps(requests), (
        "Saved data entered a provider request for an explicitly unretained caller"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("guest_active", [False, True])
@pytest.mark.parametrize("first_id", [None, "audit-caller-selected-id"])
async def test_guest_context_does_not_cross_authenticated_users(
    hass, monkeypatch, guest_active, first_id
):
    alice = await hass.auth.async_create_user("Audit Alice", group_ids=["system-admin"])
    bob = await hass.auth.async_create_user("Audit Bob", group_ids=["system-admin"])
    agent = await _agent(hass, conversation_continuity="ha_default")
    if guest_active:
        await agent._guest_mode.async_update_trusted(indefinite=True)
    wire = _install_wire(
        monkeypatch,
        agent,
        [
            _chat_sse_text("Noted."),
            _chat_sse_text("Checked."),
            _chat_sse_text("Resumed."),
        ],
    )
    first = await conversation.async_converse(
        hass=hass,
        text="My private calibration marker is audit-alice-copper.",
        conversation_id=first_id,
        context=Context(user_id=alice.id),
        language="en",
        agent_id=agent.entry.entry_id,
    )
    second = await conversation.async_converse(
        hass=hass,
        text="What did the last caller say?",
        conversation_id=first.conversation_id,
        context=Context(user_id=bob.id),
        language="en",
        agent_id=agent.entry.entry_id,
    )
    assert first.response.error_code is None and second.response.error_code is None
    assert "audit-alice-copper" not in json.dumps(wire.requests[1]["body"]), (
        "Another authenticated user inherited Alice guest history"
    )
    await conversation.async_converse(
        hass=hass,
        text="Continue my own conversation",
        conversation_id=first.conversation_id,
        context=Context(user_id=alice.id),
        language="en",
        agent_id=agent.entry.entry_id,
    )
    assert "audit-alice-copper" in json.dumps(wire.requests[2]["body"])


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [False, True])
async def test_guest_area_policy_uses_effective_entity_area(hass, monkeypatch, legacy):
    from custom_components.extended_openai_conversation_responses.guest_mode import (
        resolve_guest_policy,
    )
    from custom_components.extended_openai_conversation_responses.live_subentry_updates import (
        update_live_subentry,
    )
    from homeassistant.components.homeassistant.exposed_entities import (
        async_expose_entity,
    )
    from homeassistant.helpers import (
        area_registry as ar,
        device_registry as dr,
        entity_registry as er,
        target,
    )
    from homeassistant.helpers.template.helpers import resolve_area_id

    hall = ar.async_get(hass).async_create("Audit Hall")
    kitchen = ar.async_get(hass).async_create("Audit Kitchen")
    agent = await _agent(hass, exposed_entities_enabled=True)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=agent.entry.entry_id,
        identifiers={("audit", "hall-device")},
        name="Hall device",
    )
    dr.async_get(hass).async_update_device(device.id, area_id=hall.id)
    entity = er.async_get(hass).async_get_or_create(
        "sensor",
        "audit",
        "kitchen-override",
        config_entry=agent.entry,
        device_id=device.id,
        original_name="Audit kitchen override",
    )
    er.async_get(hass).async_update_entity(entity.entity_id, area_id=kitchen.id)
    hass.states.async_set(
        entity.entity_id,
        "audit-private-kitchen-amber",
        {"friendly_name": "Audit Kitchen override"},
    )
    async_expose_entity(hass, "conversation", entity.entity_id, True)
    assert resolve_area_id(hass, entity.entity_id) == kitchen.id
    selected = target.async_extract_referenced_entity_ids(
        hass, target.TargetSelection({"area_id": [hall.id]})
    )
    assert entity.entity_id not in selected.referenced | selected.indirectly_referenced
    options = dict(agent.subentry.data)
    if legacy:
        options.update(
            guest_policy_version=1,
            guest_readable_areas=[hall.id],
            guest_controllable_areas=[hall.id],
        )
    else:
        options.update(guest_policy_version=2, guest_excluded_areas=[hall.id])
    update_live_subentry(hass, agent.entry, agent.subentry, data=options)
    await agent._guest_mode.async_update_trusted(indefinite=True)
    policy = resolve_guest_policy(hass, agent.subentry.data, agent._guest_mode, [])
    sent = _provider(monkeypatch, agent, ["Checked."])
    result = await conversation.async_converse(
        hass=hass,
        text="Read the kitchen sensor",
        conversation_id=None,
        context=Context(),
        language="en",
        agent_id=agent.entry.entry_id,
    )
    assert result.response.error_code is None
    assert ("audit-private-kitchen-amber" in json.dumps(sent[0])) is (not legacy), (
        "Guest model prompt disagrees with actual HA area membership"
    )
    assert policy.allows_entity_read(entity.entity_id) is (not legacy), (
        "Guest area selection disagrees with HA effective-area assignment"
    )


@pytest.mark.parametrize("mode", ["device", "user"])
@pytest.mark.parametrize("guest_active", [False, True])
@pytest.mark.parametrize("reuse_id", [False, True])
@pytest.mark.parametrize("first_id", [None, "audit-selected-first-id"])
async def test_authenticated_scope_survives_guest_continuity(
    hass, monkeypatch, mode, guest_active, reuse_id, first_id
):
    alice = await hass.auth.async_create_user(
        "Guest continuity Alice", group_ids=["system-admin"]
    )
    bob = await hass.auth.async_create_user(
        "Guest continuity Bob", group_ids=["system-admin"]
    )
    agent = await _agent(hass, conversation_continuity=mode)
    if guest_active:
        await agent._guest_mode.async_update_trusted(indefinite=True)
    wire = _install_wire(
        monkeypatch,
        agent,
        [
            _chat_sse_text("Noted."),
            _chat_sse_text("Checked."),
            _chat_sse_text("Resumed."),
        ],
    )

    async def say(user, text, conversation_id):
        return await conversation.async_converse(
            hass=hass,
            text=text,
            conversation_id=conversation_id,
            context=Context(user_id=user.id),
            language="en",
            agent_id=agent.entry.entry_id,
            device_id="audit-shared-device",
        )

    first = await say(alice, "My private marker is audit-alice-turquoise.", first_id)
    second = await say(
        bob,
        "What did the previous caller say?",
        first.conversation_id if reuse_id else None,
    )
    assert first.response.error_code is None and second.response.error_code is None
    assert "audit-alice-turquoise" not in json.dumps(wire.requests[1]["body"]), (
        mode,
        guest_active,
        reuse_id,
        "Authenticated user inherited another user history",
    )
    await say(alice, "Continue my own conversation", first.conversation_id)
    assert "audit-alice-turquoise" in json.dumps(wire.requests[2]["body"])


async def test_redirect_failure_keeps_endpoint_credentials_private(
    hass, socket_enabled
):
    app = web.Application()

    async def redirect(request):
        return web.Response(status=302, headers={"Location": str(request.rel_url)})

    app.router.add_get("/loop", redirect)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    canary = "audit_endpoint_credential_canary_20261008"
    function = RestFunction()
    config = function.validate_schema(
        {"type": "rest", "resource": f"http://127.0.0.1:{port}/loop?api_key={canary}"}
    )
    try:
        with pytest.raises(HomeAssistantError) as captured:
            await function.execute(hass, config, {}, None, [])
        print("ERROR CLASS", type(captured.value).__name__)
        print("ERROR TEXT", str(captured.value))
        assert canary not in str(captured.value), (
            "REST tool failure exposes endpoint credentials to model/tool result"
        )
    finally:
        await runner.cleanup()


@pytest.mark.parametrize("mode", ["chat_completions", "responses"])
async def test_rest_credentials_never_reach_actual_provider_wire(
    hass, socket_enabled, monkeypatch, mode
):
    app = web.Application()

    async def redirect(request):
        return web.Response(status=302, headers={"Location": str(request.rel_url)})

    app.router.add_get("/loop", redirect)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    canary = "audit_endpoint_credential_canary_20261008"
    tool = {
        "spec": {
            "name": "fetch_status",
            "description": "Fetch configured status",
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {
            "type": "rest",
            "resource": f"http://127.0.0.1:{port}/loop?api_key={canary}",
        },
    }
    try:
        from tests_real_ha.test_provider_wire_e2e import (
            _responses_sse_text,
            _responses_sse_tool_call,
        )

        agent = await _agent(hass, api_mode=mode, **{CONF_FUNCTION_TOOLS: [tool]})
        wire = _install_wire(
            monkeypatch,
            agent,
            [
                _responses_sse_tool_call(name="fetch_status", tool_arguments={})
                if mode == "responses"
                else _chat_sse_tool_call(name="fetch_status", arguments={}),
                _responses_sse_text("The status endpoint failed.")
                if mode == "responses"
                else _chat_sse_text("The status endpoint failed."),
            ],
        )
        result = await _say(hass, agent, "Fetch status")
        assert result.response.error_code is None
        assert len(wire.requests) == 2
        assert canary not in json.dumps(wire.requests[0]["body"])
        assert canary not in json.dumps(wire.requests[1]["body"]), (
            "Credential leaked in model-facing REST tool result"
        )
    finally:
        await runner.cleanup()


@pytest.mark.parametrize("operation", ["update", "upsert"])
@pytest.mark.parametrize("explicit_request", [False, True])
async def test_automatic_memory_update_sensitive_provenance(
    hass, monkeypatch, operation, explicit_request
):
    owner = await hass.auth.async_create_user("Audit owner", group_ids=["system-admin"])
    agent = await _agent(hass, memory_mode="automatic", memory_auto_retrieve_limit=0)
    created = await agent._memory.async_add(
        owner.id, "User prefers tea", "preference", "implicit", key="audit.health"
    )
    # The speaker reports a fact and does not request a save or update.
    arguments = {
        "content": "User has a medical diagnosis of synthetic-condition.",
        "category": "personal",
    }
    if operation == "update":
        arguments["memory_id"] = created["memory"]["memory_id"]
        if explicit_request:
            arguments["source"] = "explicit"
    else:
        arguments.update(
            key="audit.health", source="explicit" if explicit_request else "implicit"
        )
    sent = _provider(
        monkeypatch,
        agent,
        [
            _tool(f"memory_{operation}", arguments),
            "I understand.",
        ],
    )
    response = await conversation.async_converse(
        hass=hass,
        text="Please remember that I have a medical diagnosis of synthetic-condition."
        if explicit_request
        else "I have a medical diagnosis of synthetic-condition.",
        context=Context(user_id=owner.id),
        language="en",
        agent_id=agent.entry.entry_id,
        conversation_id=None,
    )
    assert response.response.error_code is None
    records = await agent._memory.async_list(owner.id)
    print(
        json.dumps(
            {
                "operation": operation,
                "explicit_request": explicit_request,
                "requests": len(sent),
                "retained_sensitive": "medical diagnosis" in records[0].content,
                "source": records[0].source,
            }
        )
    )
    if explicit_request:
        assert "medical diagnosis" in records[0].content
        assert records[0].source == "explicit"
    else:
        assert records[0].content == "User prefers tea", (
            "Sensitive proactive update must require explicit user provenance"
        )


async def test_automatic_metadata_secret_reaches_subsequent_provider(hass, monkeypatch):
    owner = await hass.auth.async_create_user("Audit owner", group_ids=["system-admin"])
    agent = await _agent(hass, memory_mode="automatic", memory_auto_retrieve_limit=0)
    sent = _provider(
        monkeypatch,
        agent,
        [
            _tool(
                "memory_upsert",
                {
                    "content": "Harmless audit memory",
                    "category": "general",
                    "source": "implicit",
                    "subject": "password: synthetic-audit-secret",
                },
            ),
            "I understand.",
        ],
    )
    response = await conversation.async_converse(
        hass=hass,
        text="My test password is synthetic-audit-secret.",
        context=Context(user_id=owner.id),
        language="en",
        agent_id=agent.entry.entry_id,
        conversation_id=None,
    )
    assert response.response.error_code is None
    assert len(sent) == 2
    print(
        json.dumps(
            {
                "saved_record_count": len(await agent._memory.async_list(owner.id)),
                "secret_in_tool_result_sent_to_provider": "synthetic-audit-secret"
                in json.dumps(sent[1]),
            }
        )
    )
    assert await agent._memory.async_list(owner.id) == [], (
        "Privacy checks must include retained subject metadata"
    )


def _privacy_wire(monkeypatch, agent):
    """Capture all actual SDK HTTP requests, including embedding preparation."""
    import httpx

    from tests_real_ha.test_provider_wire_e2e import _raw_client, _responses_sse_text

    requests = []

    async def send(request, *args, **kwargs):
        body = json.loads(request.content.decode())
        requests.append({"path": request.url.path, "body": body})
        if request.url.path.endswith("/embeddings"):
            inputs = body["input"]
            if isinstance(inputs, str):
                inputs = [inputs]
            return httpx.Response(
                200,
                json={
                    "object": "list",
                    "model": "audit-embedding",
                    "data": [
                        {"object": "embedding", "index": index, "embedding": [1.0, 0.5]}
                        for index in range(len(inputs))
                    ],
                    "usage": {"prompt_tokens": 1, "total_tokens": 1},
                },
                request=request,
            )
        reply = (
            _responses_sse_text("Checked.")
            if request.url.path.endswith("/responses")
            else _chat_sse_text("Checked.")
        )
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=reply,
            request=request,
        )

    monkeypatch.setattr(_raw_client(agent)._client, "send", send)
    return requests


@pytest.mark.parametrize("mode", ["chat_completions", "responses"])
@pytest.mark.parametrize(
    "feature,field",
    [
        ("memory", "category"),
        ("memory", "subject"),
        ("memory", "key"),
        ("temporary", "category"),
    ],
)
async def test_previously_stored_metadata_never_enters_any_provider_request(
    hass, monkeypatch, mode, feature, field
):
    from dataclasses import asdict
    from datetime import timedelta

    from custom_components.extended_openai_conversation_responses import (
        memory as memory_module,
        temporary_memory as temporary_module,
    )
    from homeassistant.util import dt as dt_util

    owner = await hass.auth.async_create_user("Metadata owner")
    agent = await _agent(
        hass,
        api_mode=mode,
        memory_mode="manual",
        memory_retrieval_mode="hybrid",
        memory_auto_retrieve_limit=5,
        temporary_memory="balanced",
    )
    canary = "sk-syntheticauditsecret123"
    key = (agent.entry.entry_id, agent.subentry.subentry_id)
    if feature == "memory":
        created = await agent._memory.async_add(
            owner.id, "Calibration memory", "general", "explicit"
        )
        raw = asdict((await agent._memory.async_list(owner.id))[0])
        raw[field] = canary
        await agent._memory._storage.async_save({"memories": [raw]})
        hass.data[memory_module._MEMORY_MANAGERS].pop(key)
        agent._memory = await memory_module.async_get_memory(hass, *key)
    else:
        expiry = (dt_util.utcnow() + timedelta(hours=1)).isoformat()
        created = await agent._temporary_memory.async_add(
            f"user:{owner.id}",
            "Calibration memory",
            expiry,
            owner_scope_id=f"user:{owner.id}",
            source="manual",
        )
        raw = asdict(agent._temporary_memory._records[created["memory"]["memory_id"]])
        raw[field] = canary
        await agent._temporary_memory._store.async_save({"records": [raw]})
        hass.data[temporary_module._MANAGERS].pop(key)
        agent._temporary_memory = await temporary_module.async_get_temporary_memory(
            hass, *key
        )
    durable = await (
        agent._memory._storage.async_load()
        if feature == "memory"
        else agent._temporary_memory._store.async_load()
    )
    assert canary not in json.dumps(durable)
    requests = _privacy_wire(monkeypatch, agent)
    result = await conversation.async_converse(
        hass=hass,
        text="Find calibration memory",
        context=Context(user_id=owner.id),
        conversation_id=None,
        language="en",
        agent_id=agent.entry.entry_id,
    )
    assert result.response.error_code is None
    assert requests
    assert canary not in json.dumps(requests), (
        "Unsafe retained metadata reached model or embedding HTTP requests"
    )
