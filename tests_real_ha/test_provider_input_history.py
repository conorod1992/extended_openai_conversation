"""Retained native output and owned prompt slots across genuine HA requests."""

import base64
from contextlib import asynccontextmanager
import json

import httpx
from openai import AsyncOpenAI
from openai.types.responses import ResponseFunctionWebSearch, ResponseReasoningItem
import pytest
import voluptuous as vol

from custom_components.extended_openai_conversation_responses.const import DOMAIN
from custom_components.extended_openai_conversation_responses.entity import (
    _convert_content_to_responses_param,
)
from custom_components.extended_openai_conversation_responses.ha_llm_tools import (
    async_discover,
    new_reference_tool,
)
from custom_components.extended_openai_conversation_responses.ha_tool_result_compat import (
    make_tool_result_content,
)
from homeassistant.components import conversation
from homeassistant.core import Context
from homeassistant.helpers import llm
from homeassistant.helpers.chat_session import async_get_chat_session
from tests_real_ha.strict_provider import StrictProvider
from tests_real_ha.test_cross_feature_acceptance import _say, _speech
from tests_real_ha.test_entry_point_contract_matrix import _contract_agent
from tests_real_ha.test_provider_wire_e2e import (
    _chat_sse_text,
    _responses_sse_text,
    _responses_sse_tool_call,
)


@asynccontextmanager
async def _transport(entry, replies):
    """Use the SDK's supported HTTP transport injection, without replacing methods."""
    wire = StrictProvider(replies)
    original = entry.runtime_data
    async with AsyncOpenAI(
        api_key="sk-test-history",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(wire.send)),
    ) as client:
        entry.runtime_data = client
        try:
            yield wire
        finally:
            entry.runtime_data = original


async def test_native_only_history_has_valid_chat_follow_up_and_intact_call_pairs(hass):
    agent = await _contract_agent(hass)
    reasoning = ResponseReasoningItem(id="rs-history", type="reasoning", summary=[])
    search = ResponseFunctionWebSearch.model_validate(
        {
            "id": "ws-history",
            "type": "web_search_call",
            "status": "completed",
            "action": {"type": "search", "query": "history"},
        }
    )
    call = llm.ToolInput(
        id="retained-call", tool_name="previous_action", tool_args={}, external=True
    )

    async def deltas():
        yield {"role": "assistant", "native": reasoning}
        yield {"role": "assistant", "native": search}
        yield {"role": "assistant", "content": "Prior textual answer"}
        yield {"role": "assistant", "tool_calls": [call]}

    with async_get_chat_session(hass) as session:
        conversation_id = session.conversation_id
        with conversation.async_get_chat_log(hass, session) as log:
            log.async_add_user_content(
                conversation.UserContent(content="Earlier question")
            )
            retained = [
                item
                async for item in log.async_add_delta_content_stream(
                    agent.entity_id, deltas()
                )
            ]
            log.async_add_assistant_content_without_tools(
                make_tool_result_content(
                    agent_id=agent.entity_id,
                    tool_call_id=call.id,
                    tool_name=call.tool_name,
                    tool_result={"success": True},
                )
            )
    native_records = [item for item in retained if item.native is not None]
    assert len(native_records) == 2
    assert all(not item.content and not item.tool_calls for item in native_records)
    async with _transport(agent.entry, [_chat_sse_text("Follow-up answer")]) as wire:
        result = await _say(hass, agent, "Current follow-up", conversation_id)
        assert _speech(result) == "Follow-up answer"
        wire.assert_complete(1)
        messages = wire.requests[0]["body"]["messages"]
        assert {"role": "assistant"} not in messages
        assert any(item.get("content") == "Prior textual answer" for item in messages)
        assert any(item.get("content") == "Current follow-up" for item in messages)
        assert (
            next(item for item in messages if item.get("tool_calls"))["tool_calls"][0][
                "id"
            ]
            == call.id
        )
        assert (
            next(item for item in messages if item.get("role") == "tool")[
                "tool_call_id"
            ]
            == call.id
        )
    # Chat serialization cannot delete native items needed by a later Responses turn.
    assert [
        item["type"] for item in _convert_content_to_responses_param(native_records)
    ] == ["reasoning", "web_search_call"]


class HistoryTool(llm.Tool):
    name = "history_echo"
    integration = DOMAIN
    description = "Echo a value"
    parameters = vol.Schema({vol.Required("value"): str})

    def __init__(self):
        self.calls = []

    async def async_call(self, hass, tool_input, llm_context):
        self.calls.append(tool_input)
        return {"echo": tool_input.tool_args["value"]}


class HistoryAPI(llm.API):
    def __init__(self, hass, tool):
        super().__init__(hass=hass, id="history-api", name="History API")
        self.tool = tool

    async def async_get_api_instance(self, llm_context):
        return llm.APIInstance(
            api=self,
            api_prompt="HA source tool instructions",
            llm_context=llm_context,
            tools=[self.tool],
        )


@pytest.mark.parametrize("prompt", ["", '{{ "" }}', "Nonempty base prompt"])
@pytest.mark.parametrize("retained_context", [False, True])
async def test_primary_prompt_transitions_preserve_users_summaries_and_attachments(
    hass, tmp_path, prompt, retained_context
):
    tool = HistoryTool()
    llm.async_register_api(hass, HistoryAPI(hass, tool))
    context = llm.LLMContext(
        platform=DOMAIN,
        context=Context(),
        language="en",
        assistant="conversation",
        device_id=None,
    )
    snapshot = await async_discover(hass, context)
    saved = new_reference_tool(
        next(
            live.reference
            for live in snapshot.tools.values()
            if live.tool.name == tool.name
        ),
        set(),
    )
    agent = await _contract_agent(
        hass,
        api_mode="responses",
        prompt=prompt,
        functions=[saved],
        max_function_calls_per_conversation=1,
        memory_enabled=False,
        temporary_memory="off",
        archive_enabled=False,
        current_datetime_enabled=False,
        exposed_entities_enabled=False,
    )
    conversation_id = None
    if retained_context:
        path = tmp_path / "history.png"
        path.write_bytes(b"Retained image bytes")
        attachment = conversation.Attachment(
            media_content_id="local-history", mime_type="image/png", path=path
        )
        with async_get_chat_session(hass) as session:
            conversation_id = session.conversation_id
            with conversation.async_get_chat_log(hass, session) as log:
                log.content.append(
                    conversation.SystemContent(content="Retained summary context")
                )
                log.async_add_user_content(
                    conversation.UserContent(
                        content="Earlier image question", attachments=[attachment]
                    )
                )
                log.async_add_assistant_content_without_tools(
                    conversation.AssistantContent(
                        agent_id=agent.entity_id, content="Earlier image answer"
                    )
                )
    replies = [
        _responses_sse_tool_call(
            "history-call", saved["spec"]["name"], {"value": "once"}
        ),
        _responses_sse_text("Completed answer"),
    ]
    async with _transport(agent.entry, replies) as wire:
        result = await _say(hass, agent, "Current user command", conversation_id)
        assert _speech(result) == "Completed answer"
        wire.assert_complete(2)
        first, second = [request["body"]["input"] for request in wire.requests]
        first_users = [item for item in first if item.get("role") == "user"]
        assert first_users == [item for item in second if item.get("role") == "user"]
        assert first_users[-1]["content"] == "Current user command"
        assert "HA source tool instructions" in first[0]["content"]
        assert not any(
            "HA source tool instructions" in str(item.get("content", ""))
            for item in second
        )
        if prompt == "Nonempty base prompt":
            assert second[0]["content"] == prompt
        if retained_context:
            for items in (first, second):
                assert any(
                    item.get("content") == "Retained summary context" for item in items
                )
                assert base64.b64encode(b"Retained image bytes").decode() in json.dumps(
                    items
                )
    assert len(tool.calls) == 1

    from custom_components.extended_openai_conversation_responses.live_subentry_updates import (
        update_live_subentry,
    )

    for mode, new_prompt in (
        ("chat_completions", "Changed history prompt"),
        ("responses", ""),
        ("chat_completions", "Restored history prompt"),
    ):
        update_live_subentry(
            hass,
            agent.entry,
            agent.subentry,
            data={**agent.subentry.data, "api_mode": mode, "prompt": new_prompt},
        )
        await hass.async_block_till_done()
        reply = _responses_sse_text if mode == "responses" else _chat_sse_text
        async with _transport(
            agent.entry, [reply("Retained transition healthy")]
        ) as wire:
            followup = await _say(
                hass, agent, "Follow-up after prompt change", result.conversation_id
            )
            assert _speech(followup) == "Retained transition healthy"
            wire.assert_complete(1)
            serialized = json.dumps(wire.requests[0]["body"])
            assert "Current user command" in serialized
            assert "Completed answer" in serialized
            assert "history-call" in serialized
            if new_prompt:
                assert new_prompt in serialized
            if retained_context:
                assert "Retained summary context" in serialized
                assert base64.b64encode(b"Retained image bytes").decode() in serialized
        assert len(tool.calls) == 1, "Retained completed calls must not execute again"


@pytest.mark.parametrize("api_mode", ["chat_completions", "responses"])
@pytest.mark.parametrize("limit", ["count", "bytes"])
async def test_retained_attachment_ledger_bounds_replay_and_resets_for_new_session(
    hass, tmp_path, api_mode, limit
):
    """Historical replay is bounded without blocking a fresh request."""
    agent = await _contract_agent(hass, api_mode=api_mode)
    path = tmp_path / "ledger.png"
    size = 18 * 1024 * 1024 if limit == "bytes" else 12
    with path.open("wb") as handle:
        handle.truncate(size)
    count = 3 if limit == "bytes" else 11
    acknowledged = {"attachments": 0, "bytes": 0, "provider_requests": 0}
    attachment = conversation.Attachment(
        media_content_id="local-ledger", mime_type="image/png", path=path
    )
    with async_get_chat_session(hass) as session:
        cid = session.conversation_id
        with conversation.async_get_chat_log(hass, session) as log:
            for index in range(count):
                log.async_add_user_content(
                    conversation.UserContent(
                        content=f"Retained image {index}", attachments=[attachment]
                    )
                )
                acknowledged["attachments"] += 1
                acknowledged["bytes"] += size
    assert size < 20 * 1024 * 1024
    assert (
        (acknowledged["attachments"] > 10)
        if limit == "count"
        else (acknowledged["bytes"] > 50 * 1024 * 1024)
    )
    reply = _responses_sse_text if api_mode == "responses" else _chat_sse_text
    async with _transport(
        agent.entry, [reply("Available attachments reviewed")]
    ) as wire:
        result = await _say(
            hass, agent, "Read retained attachments where available", cid
        )
        assert _speech(result) == "Available attachments reviewed"
        wire.assert_complete(1)
        body = wire.requests[0]["body"]
        outgoing = body.get("input", body.get("messages"))
        parts = [
            part
            for item in outgoing
            for part in (item.get("content") or [])
            if isinstance(part, dict)
        ]
        images = [
            part for part in parts if part.get("type") in {"image_url", "input_image"}
        ]
        assert len(images) == (10 if limit == "count" else 2)
        assert len(images) * size <= 50 * 1024 * 1024
        acknowledged["provider_requests"] += len(wire.requests)
        assert "Earlier attachment omitted" in json.dumps(outgoing)
    reply = _responses_sse_text if api_mode == "responses" else _chat_sse_text
    async with _transport(agent.entry, [reply("Fresh request healthy")]) as wire:
        healthy = await _say(hass, agent, "Independent request")
        assert _speech(healthy) == "Fresh request healthy"
        wire.assert_complete(1)
        acknowledged["provider_requests"] += len(wire.requests)
    assert acknowledged == {
        "attachments": count,
        "bytes": count * size,
        "provider_requests": 2,
    }


def _multi_message_response(parts, *, streaming=True):
    """Real SDK output with independent commentary/final message identities."""
    from tests_real_ha.test_provider_wire_e2e import _response_object

    output = [
        {
            "id": f"msg-multi-{i}",
            "type": "message",
            "role": "assistant",
            "status": "completed",
            **({"phase": phase} if phase else {}),
            "content": [
                {"type": "output_text", "text": text, "annotations": [], "logprobs": []}
            ],
        }
        for i, (phase, text) in enumerate(parts)
    ]
    response = _response_object("resp-multi", output)
    if not streaming:
        return (200, response)
    events = []
    for i, item in enumerate(output):
        events.extend(
            [
                {
                    "type": "response.output_item.added",
                    "output_index": i,
                    "item": {**item, "content": [], "status": "in_progress"},
                },
                {
                    "type": "response.output_text.delta",
                    "output_index": i,
                    "content_index": 0,
                    "item_id": item["id"],
                    "delta": parts[i][1],
                    "logprobs": [],
                },
                {"type": "response.output_item.done", "output_index": i, "item": item},
            ]
        )
    events.append({"type": "response.completed", "response": response})
    return "".join(
        f"data: {json.dumps({**event, 'sequence_number': i})}\n\n"
        for i, event in enumerate(events)
    ).encode()


@pytest.mark.parametrize("streaming", [True, False], ids=["streamed", "completed"])
@pytest.mark.parametrize("phase", [None, "final_answer"])
async def test_public_responses_preserves_all_final_messages_in_reply_archive_and_replay(
    hass, streaming, phase
):
    from pytest_homeassistant_custom_component.common import MockUser

    user = MockUser(is_owner=True).add_to_hass(hass)
    agent = await _contract_agent(
        hass,
        api_mode="responses",
        archive_enabled=True,
        chat_model="gpt-5.6" if streaming else "gpt-5.5-pro",
        reasoning_effort="none" if streaming else "medium",
    )
    parts = [
        ("commentary", "Checking context."),
        (phase, "First answer."),
        ("commentary", "More progress."),
        (phase, "Second answer."),
    ]
    async with _transport(
        agent.entry, [_multi_message_response(parts, streaming=streaming)]
    ) as wire:
        first = await conversation.async_converse(
            hass=hass,
            text="Question",
            conversation_id=None,
            context=Context(user_id=user.id),
            language="en",
            agent_id=agent.entry.entry_id,
        )
        assert _speech(first) == "First answer.\n\nSecond answer."
        wire.assert_complete(1)
    archive = await agent._archive.async_backup_data()
    assert archive["turns"][0]["assistant_text"] == "First answer.\n\nSecond answer."
    async with _transport(
        agent.entry, [_multi_message_response([(phase, "Next")], streaming=streaming)]
    ) as wire:
        followup = await conversation.async_converse(
            hass=hass,
            text="Next question",
            conversation_id=first.conversation_id,
            context=Context(user_id=user.id),
            language="en",
            agent_id=agent.entry.entry_id,
        )
        assert _speech(followup) == "Next"
        retained = wire.requests[0]["body"]["input"]
        messages = [
            item
            for item in retained
            if str(item.get("id", "")).startswith("msg-multi-")
        ]
        assert [item["id"] for item in messages] == [f"msg-multi-{i}" for i in range(4)]
        assert [item["content"][0]["text"] for item in messages] == [
            text for _, text in parts
        ]


@pytest.mark.parametrize("mode", ["chat_completions", "responses"])
@pytest.mark.parametrize(
    "kind", ["missing-image", "missing-pdf", "pdf-after-api-change"]
)
async def test_historical_attachment_unavailability_does_not_break_public_followup(
    hass, tmp_path, mode, kind
):
    agent = await _contract_agent(hass, api_mode=mode)
    path = tmp_path / ("historical.pdf" if "pdf" in kind else "historical.png")
    if kind == "pdf-after-api-change":
        path.write_bytes(b"%PDF-1.4\n%%EOF")
    with async_get_chat_session(hass) as session:
        cid = session.conversation_id
        with conversation.async_get_chat_log(hass, session) as log:
            attachment = conversation.Attachment(
                media_content_id="historical",
                mime_type="application/pdf" if "pdf" in kind else "image/png",
                path=path,
            )
            log.async_add_user_content(
                conversation.UserContent(
                    content="Earlier attachment question", attachments=[attachment]
                )
            )
            log.content.append(
                conversation.AssistantContent(
                    agent_id=agent.entity_id, content="Earlier answer"
                )
            )
    reply = _responses_sse_text if mode == "responses" else _chat_sse_text
    async with _transport(agent.entry, [reply("Follow-up is healthy")]) as wire:
        result = await _say(hass, agent, "Text-only follow-up", cid)
        assert _speech(result) == "Follow-up is healthy"
        wire.assert_complete(1)
        text = json.dumps(wire.requests[0]["body"])
        assert "Earlier attachment question" in text and "Earlier answer" in text
        assert "Text-only follow-up" in text
        if mode == "responses" and kind == "pdf-after-api-change":
            assert "input_file" in text
        else:
            assert "Earlier attachment omitted" in text
            assert str(path) not in text
    # Preparing one request does not destroy the retained attachment metadata.
    assert log.content[1].attachments == [attachment]


@pytest.mark.parametrize("mode", ["chat_completions", "responses"])
async def test_full_handler_bulk_context_measurement_runs_in_workers(
    hass, monkeypatch, mode
):
    import threading
    from custom_components.extended_openai_conversation_responses import (
        context_usage_hardening as measurement,
    )

    agent = await _contract_agent(
        hass,
        api_mode=mode,
        prompt="System",
        current_datetime_template="",
        context_threshold=3000,
        context_truncate_strategy="keep_recent",
        memory_mode="off",
        temporary_memory="off",
    )
    with async_get_chat_session(hass) as session:
        cid = session.conversation_id
        with conversation.async_get_chat_log(hass, session) as log:
            for i in range(96):
                call = llm.ToolInput(
                    id=f"retained-{i}", tool_name="report", tool_args={}, external=True
                )
                log.content.extend(
                    [
                        conversation.UserContent(content=f"Older question {i}"),
                        conversation.AssistantContent(
                            agent_id=agent.entity_id, tool_calls=[call]
                        ),
                        make_tool_result_content(
                            agent_id=agent.entity_id,
                            tool_call_id=call.id,
                            tool_name=call.tool_name,
                            tool_result={"result": {"value": "x" * 16000}},
                        ),
                        conversation.AssistantContent(
                            agent_id=agent.entity_id, content="Reported"
                        ),
                    ]
                )
    main = threading.get_ident()
    calls = []
    original = measurement.estimate_provider_input_tokens

    def checked(items, tools=None):
        calls.append((threading.get_ident(), len(items)))
        return original(items, tools)

    monkeypatch.setattr(measurement, "estimate_provider_input_tokens", checked)
    reply = _responses_sse_text if mode == "responses" else _chat_sse_text
    async with _transport(agent.entry, [reply("Current answer")]) as wire:
        result = await _say(hass, agent, "Current question", cid)
        assert _speech(result) == "Current answer"
        wire.assert_complete(1)
        assert "Current question" in json.dumps(wire.requests[0]["body"])
        assert "Older question" not in json.dumps(wire.requests[0]["body"])
    assert calls and main not in [thread for thread, _ in calls]
    assert len(calls) <= 15


@pytest.mark.parametrize("mode", ["chat_completions", "responses"])
async def test_foreground_preflight_summary_belongs_to_current_usage_run(hass, mode):
    from tests_real_ha.test_provider_wire_e2e import _response_object

    agent = await _contract_agent(
        hass,
        api_mode=mode,
        prompt="System",
        current_datetime_template="",
        context_threshold=3000,
        context_truncate_strategy="summarize",
        memory_mode="off",
        temporary_memory="off",
    )
    with async_get_chat_session(hass) as session:
        cid = session.conversation_id
        with conversation.async_get_chat_log(hass, session) as log:
            log.content.extend(
                [
                    conversation.UserContent(content="Old content " + "x" * 20000),
                    conversation.AssistantContent(
                        agent_id=agent.entity_id, content="Old answer"
                    ),
                ]
            )
    if mode == "responses":
        summary = _response_object(
            "summary",
            [
                {
                    "id": "summary-message",
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [
                        {
                            "type": "output_text",
                            "text": "A compact summary",
                            "annotations": [],
                        }
                    ],
                }
            ],
        )
        summary["usage"] = {
            "input_tokens": 101,
            "output_tokens": 7,
            "total_tokens": 108,
        }
        events = [
            json.loads(chunk.removeprefix("data: "))
            for chunk in _responses_sse_text("Answer").decode().strip().split("\n\n")
        ]
        events[-1]["response"]["usage"] = {
            "input_tokens": 11,
            "output_tokens": 3,
            "total_tokens": 14,
        }
        main = "".join(f"data: {json.dumps(e)}\n\n" for e in events).encode()
    else:
        summary = {
            "id": "summary",
            "object": "chat.completion",
            "created": 0,
            "model": "gpt-5.6",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "A compact summary"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 101,
                "completion_tokens": 7,
                "total_tokens": 108,
            },
        }
        usage = {
            "id": "main",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "gpt-5.6",
            "choices": [],
            "usage": {"prompt_tokens": 11, "completion_tokens": 3, "total_tokens": 14},
        }
        main = _chat_sse_text("Answer").replace(
            b"data: [DONE]", f"data: {json.dumps(usage)}\n\ndata: [DONE]".encode()
        )
    async with _transport(agent.entry, [(200, summary), main]) as wire:
        result = await _say(hass, agent, "Current question", cid)
        assert _speech(result) == "Answer"
        wire.assert_complete(2)
        assert "A compact summary" in json.dumps(wire.requests[1]["body"])
    requests = agent._usage.requests
    assert [r.request_stage for r in requests] == ["context_summary", "initial"]
    run = agent._usage.runs[-1]
    assert {r.run_id for r in requests} == {run.run_id}
    assert (run.request_count, run.successful_request_count) == (2, 2)
    assert (run.input_tokens, run.output_tokens, run.total_tokens) == (112, 10, 122)
    assert agent._usage.totals.total_tokens == 122


@pytest.mark.parametrize(
    "pdf_count,pdf_size", [(0, 0), (10, 700), (3, 18 * 1024 * 1024)]
)
async def test_incompatible_historical_pdfs_do_not_consume_chat_image_budget(
    hass, tmp_path, monkeypatch, pdf_count, pdf_size
):
    agent = await _contract_agent(hass, api_mode="chat_completions")
    image = tmp_path / "retained.png"
    image.write_bytes(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aV1cAAAAASUVORK5CYII="
        )
    )
    pdf = tmp_path / "retained.pdf"
    pdf.write_bytes(
        b"%PDF-1.4\n1 0 obj\n<</Type/Catalog/Pages 2 0 R>>\nendobj\n2 0 obj\n<</Type/Pages/Count 0/Kids[]>>\nendobj\ntrailer\n<</Root 1 0 R>>\n%%EOF\n"
    )
    if pdf_size:
        with pdf.open("ab") as handle:
            handle.truncate(pdf_size)
    from custom_components.extended_openai_conversation_responses import (
        entity as entity_module,
    )

    read_paths = []
    original_read = entity_module.read_bounded_local_file

    def read(path, total=0):
        read_paths.append(path)
        return original_read(path, total)

    monkeypatch.setattr(entity_module, "read_bounded_local_file", read)
    with async_get_chat_session(hass) as session:
        cid = session.conversation_id
        with conversation.async_get_chat_log(hass, session) as log:
            log.async_add_user_content(
                conversation.UserContent(
                    content="Look at this image",
                    attachments=[
                        conversation.Attachment(
                            media_content_id="image", mime_type="image/png", path=image
                        )
                    ],
                )
            )
            log.content.append(
                conversation.AssistantContent(
                    agent_id=agent.entity_id, content="Image reviewed"
                )
            )
            if pdf_count:
                log.async_add_user_content(
                    conversation.UserContent(
                        content="Later PDFs",
                        attachments=[
                            conversation.Attachment(
                                media_content_id=f"pdf-{i}",
                                mime_type="application/pdf",
                                path=pdf,
                            )
                            for i in range(pdf_count)
                        ],
                    )
                )
                log.content.append(
                    conversation.AssistantContent(
                        agent_id=agent.entity_id, content="PDFs reviewed in Responses"
                    )
                )
    async with _transport(agent.entry, [_chat_sse_text("Answer")]) as wire:
        result = await _say(hass, agent, "Recall the image", cid)
        assert _speech(result) == "Answer"
        outgoing = wire.requests[0]["body"]["messages"]
        parts = [
            part
            for item in outgoing
            for part in (item.get("content") or [])
            if isinstance(part, dict)
        ]
        images = [part for part in parts if part.get("type") == "image_url"]
        assert not any(part.get("type") == "input_file" for part in parts)
        assert len(images) == 1, (
            "Incompatible PDFs must not evict an otherwise usable historical image"
        )
        assert read_paths == [image]
