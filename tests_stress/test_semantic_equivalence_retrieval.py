"""Independent ranking models and equivalent public journeys over real native stores."""

from copy import deepcopy
import json
import math
import random

from aiohttp import web
import pytest
from pytest_homeassistant_custom_component.common import MockUser

from custom_components.extended_openai_conversation_responses.knowledge import (
    HomeAssistantKnowledgeStorage,
    KnowledgeLibrary,
)
from custom_components.extended_openai_conversation_responses.memory import (
    HomeAssistantEmbeddingCacheStorage,
    HomeAssistantMemoryStorage,
    PersistentMemory,
)
from homeassistant.components import conversation
from homeassistant.core import Context
from tests.functions.behaviour_generators import assert_typed_value
from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry
from tests_real_ha.test_management_backend_acceptance import (
    _admin_client,
    _management_call,
)
from tests_real_ha.test_provider_wire_e2e import (
    _chat_sse_text,
    _chat_sse_tool_call,
    _responses_sse_text,
    _responses_sse_tool_call,
    _speech,
)
from tests_stress.conftest import record
from tests_stress.test_memory_knowledge_scale import _embedding_reply
from tests_stress.test_os_storage_faults import real_store_io  # noqa: F401

OWNER = "semantic-reference-owner"
QUERY = "zzzzzz"


def allowed_ranking(actual, scores, *, limit, offset=0):
    """Compare score groups, preserving every meaningful field and legitimate tie."""
    ranked = sorted(scores.values(), reverse=True)
    expected = ranked[offset : offset + limit]
    assert len(actual) == len(expected)
    assert len(actual) == len(set(actual))
    assert all(item in scores for item in actual)
    assert [scores[item] for item in actual] == expected
    if offset == 0 and expected:
        mandatory = {item for item, score in scores.items() if score > expected[-1]}
        assert mandatory <= set(actual)


def cosine(vector):
    # Independent two-dimensional geometry for the reviewed [1,0] query.
    return vector[0] / math.hypot(*vector)


def test_ranking_oracle_rejects_swapped_associations_stale_results_and_wrong_ties():
    scores = {"first": 1.0, "tie-a": 0.8, "tie-b": 0.8, "last": 0.6}
    for tie in ("tie-a", "tie-b"):
        allowed_ranking(["first", tie], scores, limit=2)
    for wrong in (
        ["last", "first"],
        ["tie-a", "tie-b"],
        ["first", "first"],
        ["first", "deleted"],
    ):
        with pytest.raises(AssertionError):
            allowed_ranking(wrong, scores, limit=2)


async def endpoint(provider, embeddings=None):
    app = web.Application()
    app.router.add_post("/v1/chat/completions", provider)
    app.router.add_post("/v1/responses", provider)
    if embeddings:
        app.router.add_post("/v1/embeddings", embeddings)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    return runner, f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"


async def say(hass, entry, text):
    return await conversation.async_converse(
        hass=hass,
        text=text,
        conversation_id=None,
        context=Context(user_id=OWNER),
        language="en",
        agent_id=entry.entry_id,
    )


@pytest.mark.usefixtures("real_store_io")
@pytest.mark.parametrize("mode", ["chat_completions", "responses"])
async def test_controlled_semantic_corpus_survives_edits_cache_and_reconstruction(
    hass, hass_ws_client, socket_enabled, stress_seed, stress_trace, mode
):
    del socket_enabled
    MockUser(id=OWNER, is_owner=True).add_to_hass(hass)
    rng = random.Random(stress_seed)
    vectors = {}
    model = {}
    embedding_inputs, provider_bodies = [], []
    ranking_checks = pagination_checks = 0

    async def embeddings(request):
        body = await request.json()
        assert body["model"] == "controlled-ranking-model"
        values = body["input"]
        embedding_inputs.extend(values)
        result = []
        for value in values:
            if value == QUERY:
                result.append([1.0, 0.0])
            else:
                matches = [vector for text, vector in vectors.items() if text in value]
                assert len(matches) == 1, value
                result.append(matches[0])
        indices = list(range(len(result)))
        rng.shuffle(indices)
        _, payload = _embedding_reply([result[index] for index in indices], indices)
        return web.json_response(payload)

    async def provider(request):
        provider_bodies.append(await request.json())
        return web.Response(
            body=(
                _chat_sse_text if mode == "chat_completions" else _responses_sse_text
            )("Ranking healthy"),
            content_type="text/event-stream",
        )

    runner, url = await endpoint(provider, embeddings)
    entry = _make_entry(
        "Controlled semantic ranking",
        include_ai_task=False,
        base_url=url + "/v1",
        conversation_options={
            "api_mode": mode,
            "chat_model": "gpt-5.6",
            "reasoning_effort": "none",
            "memory_mode": "manual",
            "memory_retrieval_mode": "hybrid",
            "memory_embedding_model": "controlled-ranking-model",
            "memory_auto_retrieve_limit": 3,
            "current_datetime_enabled": False,
            "exposed_entities_enabled": False,
        },
    )
    try:
        await _setup_entry(hass, entry)
        agent = conversation.async_get_agent(hass, entry.entry_id)
        client = await _admin_client(hass, hass_ws_client)
        insertion = list(range(32))
        rng.shuffle(insertion)
        for index in insertion:
            content = f"record_{index:03d}_revision_initial data"
            vector = (
                [-1.0, 0.0]
                if index == 30
                else [0.25, 1.0]
                if index == 31
                else [3.0, 1.0]
                if index in (5, 6)
                else [1.0 + index / 4, 1.0]
            )
            vectors[content] = vector
            created = await _management_call(
                client,
                entry=entry,
                section="memories",
                action="add",
                scope_id="user:" + OWNER,
                content=content,
                category="reference",
                key=f"rank-{index}",
            )
            model[created["memory"]["memory_id"]] = {
                "content": content,
                "vector": vector,
                "index": index,
            }

        def scores():
            return {
                identity: cosine(item["vector"])
                for identity, item in model.items()
                if cosine(item["vector"]) >= 0.55
            }

        async def check(manager, phase):
            nonlocal ranking_checks, pagination_checks
            assert await manager.async_search(OWNER, QUERY, limit=50) == []
            before = len([value for value in embedding_inputs if value != QUERY])
            query = await manager.async_prepare_hybrid([OWNER], QUERY)
            assert_typed_value(query, [1.0, 0.0])
            actual = await manager.async_search(
                OWNER, QUERY, limit=50, query_embedding=query, hybrid=True
            )
            allowed_ranking([item.memory_id for item in actual], scores(), limit=50)
            pages = []
            for offset in range(0, len(scores()), 7):
                page = await manager.async_search(
                    OWNER,
                    QUERY,
                    limit=7,
                    offset=offset,
                    query_embedding=query,
                    hybrid=True,
                )
                allowed_ranking(
                    [item.memory_id for item in page], scores(), limit=7, offset=offset
                )
                pages.extend(item.memory_id for item in page)
                pagination_checks += 1
            assert len(pages) == len(set(pages)) == len(scores())
            assert manager.hybrid_status()["status"] == "active"
            record(
                stress_trace,
                "controlled_ranking",
                phase=phase,
                corpus=len(model),
                eligible=len(scores()),
                page_count=math.ceil(len(scores()) / 7),
                new_document_vectors=len(
                    [value for value in embedding_inputs if value != QUERY]
                )
                - before,
            )
            ranking_checks += 1
            return actual

        first = await check(agent._memory, "cold")
        document_count = len([value for value in embedding_inputs if value != QUERY])
        assert document_count == 32
        assert [item.memory_id for item in await check(agent._memory, "warm")] == [
            item.memory_id for item in first
        ]
        assert (
            len([value for value in embedding_inputs if value != QUERY])
            == document_count
        )
        moved_id = next(
            identity for identity, item in model.items() if item["index"] == 29
        )
        changed = "record_029_revision_changed data"
        vectors[changed] = [0.75, 1.0]
        await _management_call(
            client,
            entry=entry,
            section="memories",
            action="update",
            scope_id="user:" + OWNER,
            memory_id=moved_id,
            content=changed,
        )
        model[moved_id].update(content=changed, vector=vectors[changed])
        changed_rank = await check(agent._memory, "content-edit")
        assert changed_rank[-1].memory_id == moved_id
        assert (
            len([value for value in embedding_inputs if value != QUERY])
            == document_count + 1
        )
        removed = next(
            identity for identity, item in model.items() if item["index"] == 28
        )
        deletion = await _management_call(
            client,
            entry=entry,
            section="memories",
            action="delete",
            scope_id="user:" + OWNER,
            memory_id=removed,
        )
        assert deletion["deleted"] == 1
        del model[removed]
        await check(agent._memory, "delete")
        for cached in (True, False):
            rebuilt = PersistentMemory(
                HomeAssistantMemoryStorage(
                    hass, entry.entry_id, agent.subentry.subentry_id
                ),
                HomeAssistantEmbeddingCacheStorage(
                    hass, entry.entry_id, agent.subentry.subentry_id
                )
                if cached
                else None,
            )
            await rebuilt.async_initialize()
            rebuilt.set_embedding_provider(
                agent._async_create_embeddings, "controlled-ranking-model"
            )
            await check(rebuilt, "persisted-cache" if cached else "reconstructed-index")
        result = await say(hass, entry, QUERY)
        assert _speech(result) == "Ranking healthy"
        serialized = json.dumps(provider_bodies[-1])
        top = sorted(scores(), key=scores().get, reverse=True)[:3]
        assert all(model[identity]["content"] in serialized for identity in top)
        assert (
            changed not in serialized
            and "record_028_revision_initial" not in serialized
        )
        record(
            stress_trace,
            "summary",
            controlled_retrieval_journeys=1,
            controlled_ranking_checks=ranking_checks,
            independent_index_reconstructions=2,
            retrieval_pagination_checks=pagination_checks,
            public_turns=1,
        )
    finally:
        await runner.cleanup()


def terms(count):
    return (
        "bridge"
        if count == 0
        else "orion bridge"
        if count == 1
        else "orion bridge cobalt"
    )


@pytest.mark.usefixtures("real_store_io")
async def test_competing_knowledge_scores_track_edits_disabled_sources_and_rebuild(
    hass, stress_seed, stress_trace
):
    storage = HomeAssistantKnowledgeStorage(hass, "controlled-knowledge", "agent")
    library = KnowledgeLibrary(storage)
    await library.async_initialize()
    model = {}
    combinations = [
        (title, description, content)
        for title in range(3)
        for description in range(3)
        for content in (1, 2)
    ]
    random.Random(stress_seed).shuffle(combinations)
    for index, (title, description, content) in enumerate(combinations):
        source = await library.async_create(
            f"Source {index} {terms(title)}",
            terms(description),
            terms(content),
            enabled=True,
        )
        model[source.source_id] = {
            "score": max(title, description, content) / 2 + 4 * title + 2 * description,
            "enabled": True,
        }

    async def check(candidate, phase):
        expected = {
            identity: item["score"]
            for identity, item in model.items()
            if item["enabled"]
        }
        result = await candidate.async_search("orion cobalt", limit=10)
        allowed_ranking([item.source_id for item in result], expected, limit=10)
        assert [item.score for item in result] == [
            expected[item.source_id] for item in result
        ]
        record(
            stress_trace,
            "knowledge_ranking",
            phase=phase,
            competitors=len(model),
            eligible=len(expected),
            scores=[item.score for item in result],
        )
        return result

    first = await check(library, "cold")
    await check(library, "warm")
    changed = first[0].source_id
    await library.async_update(
        changed,
        title="Unrelated title",
        description="Unrelated description",
        content="orion bridge",
    )
    model[changed]["score"] = 0.5
    assert changed not in [item.source_id for item in await check(library, "edit")]
    disabled = first[1].source_id
    await library.async_update(disabled, enabled=False)
    model[disabled]["enabled"] = False
    await check(library, "disable")
    deleted = first[2].source_id
    assert await library.async_delete(deleted)
    del model[deleted]
    await check(library, "delete")
    rebuilt = KnowledgeLibrary(
        HomeAssistantKnowledgeStorage(hass, "controlled-knowledge", "agent")
    )
    await rebuilt.async_initialize()
    await check(rebuilt, "reconstructed-index")
    await library.async_update(disabled, enabled=True)
    model[disabled]["enabled"] = True
    await check(library, "reenable")
    record(
        stress_trace,
        "summary",
        controlled_retrieval_journeys=1,
        controlled_ranking_checks=7,
        independent_index_reconstructions=1,
        disabled_source_ranking_checks=2,
    )


def semantic_schema(value):
    """Only declared order-insensitive schema arrays may change order."""
    if isinstance(value, dict):
        return {
            key: sorted(
                (semantic_schema(item) for item in child),
                key=lambda item: (
                    type(item).__name__,
                    json.dumps(item, sort_keys=True),
                ),
            )
            if key in {"enum", "required"} and isinstance(child, list)
            else semantic_schema(child)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [semantic_schema(item) for item in value]
    return value


def test_semantic_comparison_preserves_types_and_meaningful_fields():
    assert semantic_schema({"enum": [False, 0, "0"]}) == semantic_schema(
        {"enum": ["0", 0, False]}
    )
    for changed in ({"enum": [0, "0"]}, {"enum": [False, 0, "0"], "default": False}):
        with pytest.raises(AssertionError):
            assert_typed_value(
                semantic_schema(changed), semantic_schema({"enum": [False, 0, "0"]})
            )


@pytest.mark.usefixtures("real_store_io")
@pytest.mark.parametrize("mode", ["chat_completions", "responses"])
async def test_equivalent_public_installations_preserve_context_tools_and_effects(
    hass, hass_ws_client, socket_enabled, stress_trace, mode
):
    del socket_enabled
    MockUser(id=OWNER, is_owner=True).add_to_hass(hass)
    effects, bodies = [], []
    expected_effect = {"flag": False, "zero": 0, "empty": []}
    tool = {
        "spec": {
            "name": "equivalence_probe",
            "description": "Execute the controlled business result",
            "parameters": {
                "type": "object",
                "properties": {
                    "flag": {"type": "boolean", "enum": [False, True]},
                    "zero": {"type": "integer", "const": 0},
                },
                "required": ["flag", "zero"],
                "additionalProperties": False,
            },
        },
        "function": {
            "type": "script",
            "sequence": [
                {
                    "action": "semantic_probe.record",
                    "data": {"flag": "{{ flag }}", "zero": "{{ zero }}", "empty": []},
                },
                {"variables": {"_function_result": expected_effect}},
            ],
        },
    }

    async def effect(call):
        effects.append(dict(call.data))

    hass.services.async_register("semantic_probe", "record", effect)

    async def provider(request):
        body = await request.json()
        bodies.append(body)
        messages = body.get("messages", body.get("input", []))
        result = next(
            (
                item
                for item in messages
                if item.get("role") == "tool"
                or item.get("type") == "function_call_output"
            ),
            None,
        )
        if result:
            assert_typed_value(
                json.loads(result.get("content", result.get("output")))["result"],
                expected_effect,
            )
            payload = (
                _chat_sse_text if mode == "chat_completions" else _responses_sse_text
            )("Equivalent result")
        else:
            payload = (
                _chat_sse_tool_call
                if mode == "chat_completions"
                else _responses_sse_tool_call
            )("equivalent-call", "equivalence_probe", {"flag": False, "zero": 0})
        return web.Response(body=payload, content_type="text/event-stream")

    runner, url = await endpoint(provider)
    client = None
    baseline = None
    baseline_data = baseline_memory = None
    try:
        for history in (
            "cold",
            "warm-and-edited",
            "unrelated-save",
            "persisted-reconstruction",
        ):
            variant = deepcopy(tool)
            if history != "cold":
                parameters = variant["spec"]["parameters"]
                parameters["properties"] = dict(
                    reversed(list(parameters["properties"].items()))
                )
                parameters["required"].reverse()
                parameters["properties"]["flag"]["enum"].reverse()
            options = {
                "api_mode": mode,
                "chat_model": "gpt-5.6",
                "reasoning_effort": "none",
                "prompt": "Controlled equivalent installation",
                "functions": [variant],
                "memory_mode": "manual",
                "memory_auto_retrieve_limit": 1,
                "current_datetime_enabled": False,
                "exposed_entities_enabled": False,
                "guest_mode_enabled": False,
            }
            if history == "persisted-reconstruction":
                options = deepcopy(baseline_data)
            entry = _make_entry(
                "Equivalent installation",
                include_ai_task=False,
                base_url=url + "/v1",
                conversation_options=options,
            )
            await _setup_entry(hass, entry)
            if client is None:
                client = await _admin_client(hass, hass_ws_client)
            agent = conversation.async_get_agent(hass, entry.entry_id)
            if history == "persisted-reconstruction":
                records = PersistentMemory.validate_backup_data(baseline_memory)
                await agent._memory.async_replace_backup(records)
                memory_id = records[0].memory_id
            else:
                created = await agent._memory.async_add(
                    OWNER,
                    "Calibration setting is outdated"
                    if history == "warm-and-edited"
                    else "Calibration setting is cobalt",
                    "reference",
                    "explicit",
                    key="calibration",
                )
                memory_id = created["memory"]["memory_id"]
            if history == "warm-and-edited":
                assert (
                    _speech(await say(hass, entry, "calibration"))
                    == "Equivalent result"
                )
                assert "Calibration setting is outdated" in json.dumps(bodies[-2])
                await agent._memory.async_update(
                    OWNER, memory_id, content="Calibration setting is cobalt"
                )
            if history == "unrelated-save":
                config = await _management_call(
                    client, entry=entry, section="configuration", action="get"
                )
                saved = await _management_call(
                    client,
                    entry=entry,
                    section="configuration",
                    action="update",
                    config={"usage_request_retention_days": 90},
                    revision=config["revision"],
                )
                assert saved["config"]["usage_request_retention_days"] == 90
            start, effect_start = len(bodies), len(effects)
            assert _speech(await say(hass, entry, "calibration")) == "Equivalent result"
            assert_typed_value(effects[effect_start:], [expected_effect])
            requests = deepcopy(bodies[start:])
            assert len(requests) == 2
            assert "Calibration setting is cobalt" in json.dumps(requests[0])
            assert "Calibration setting is outdated" not in json.dumps(requests)
            # Only this known record identifier varies. No fields are omitted.
            serialized = json.dumps(requests).replace(
                memory_id, "<calibration-memory-id>"
            )
            normalized = json.loads(serialized)
            for request in normalized:
                for item in request.get("tools", []):
                    schema = item.get("function", item).get("parameters")
                    if schema is not None:
                        item.get("function", item)["parameters"] = semantic_schema(
                            schema
                        )
            if baseline is None:
                baseline = normalized
                baseline_data = deepcopy(dict(agent.subentry.data))
                baseline_memory = await HomeAssistantMemoryStorage(
                    hass, entry.entry_id, agent.subentry.subentry_id
                ).async_load()
                assert (
                    baseline_memory["memories"][0]["content"]
                    == "Calibration setting is cobalt"
                )
            else:
                for number, (left, right) in enumerate(
                    zip(normalized, baseline, strict=True)
                ):
                    for key in left.keys() & right.keys():
                        if isinstance(left[key], list) and isinstance(right[key], list):
                            assert len(left[key]) == len(right[key]), (
                                history,
                                number,
                                key,
                                {
                                    item.get("function", item).get("name")
                                    for item in left[key]
                                }
                                - {
                                    item.get("function", item).get("name")
                                    for item in right[key]
                                },
                            )
                assert_typed_value(normalized, baseline)
            record(
                stress_trace,
                "equivalent_history",
                history=history,
                provider_requests=2,
                actual_effects=1,
                whitelisted_identifiers=["calibration-memory-id"],
            )
        record(
            stress_trace,
            "summary",
            semantic_equivalence_histories=4,
            equivalent_public_journeys=4,
            public_turns=5,
        )
    finally:
        await runner.cleanup()
