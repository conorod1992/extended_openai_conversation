"""Real Home Assistant acceptance tests for the AI Task runtime path."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import replace
import inspect
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, MockUser
import voluptuous as vol

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
    CONF_API_MODE,
    CONF_FUNCTION_TOOLS,
    CONF_SKIP_AUTHENTICATION,
    CONFIG_ENTRY_VERSION,
    DEFAULT_AI_TASK_OPTIONS,
    DEFAULT_MAX_FUNCTION_CALLS_PER_CONVERSATION,
    DOMAIN,
    SERVICE_CALL_FUNCTION,
    SERVICE_PROCESS,
)
from custom_components.extended_openai_conversation_responses.ha_llm_tools import (
    ToolSnapshot,
    current_snapshot,
    new_reference_tool,
    reference_key,
    tool_snapshot_scope,
)
from homeassistant.components import ai_task, conversation, media_source
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_API_KEY
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er, llm


class FakeStream:
    """Small async iterator matching the OpenAI streaming boundary."""

    def __init__(self, chunks: list[Any]) -> None:
        self._chunks = chunks

    async def __aiter__(self) -> AsyncIterator[Any]:
        for chunk in self._chunks:
            yield chunk


def _chunk(
    *,
    content: str | None = None,
    finish_reason: str | None = "stop",
    tool_calls: list[Any] | None = None,
) -> Any:
    """Build the Chat Completions fields consumed by the integration."""
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(
                    content=content,
                    refusal=None,
                    tool_calls=tool_calls,
                ),
                finish_reason=finish_reason,
            )
        ],
        usage=None,
    )


def _tool_delta(index: int, name: str, arguments: str, call_id: str) -> Any:
    return SimpleNamespace(
        index=index,
        id=call_id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


Outcome = str | BaseException | Callable[[dict[str, Any]], list[Any]]


class FakeCompletions:
    """Deterministic fake at the provider SDK boundary."""

    def __init__(self, outcomes: list[Outcome]) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> FakeStream:
        self.calls.append(kwargs)
        if not self.outcomes:
            raise AssertionError("Unexpected provider request")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        if callable(outcome):
            return FakeStream(outcome(kwargs))
        return FakeStream([_chunk(content=outcome)])


class FakeClient:
    """Only the Chat Completions SDK surface used by these tests."""

    def __init__(self, outcomes: list[Outcome]) -> None:
        self.completions = FakeCompletions(outcomes)
        self.chat = SimpleNamespace(completions=self.completions)


class ContextProbeTool(llm.Tool):
    """Caller-owned HA LLM tool that records the authoritative request context."""

    name = "context_probe"
    description = "Return the supplied value and record the Home Assistant context."
    parameters = vol.Schema({vol.Required("value"): str})

    def __init__(self) -> None:
        self.calls: list[tuple[llm.ToolInput, llm.LLMContext]] = []

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> dict[str, Any]:
        self.calls.append((tool_input, llm_context))
        return {"echo": tool_input.tool_args["value"]}


class CallerAPI(llm.API):
    """Minimal caller-supplied HA LLM API for the genuine AI Task path."""

    tools: list[llm.Tool]

    async def async_get_api_instance(self, llm_context: llm.LLMContext) -> llm.APIInstance:
        return llm.APIInstance(
            api=self,
            api_prompt="Caller API prompt.",
            llm_context=llm_context,
            tools=list(self.tools),
        )


def _entry(api_mode: str = API_MODE_CHAT_COMPLETIONS) -> MockConfigEntry:
    """Build one local-only integration entry with one AI Task subentry."""
    options = dict(DEFAULT_AI_TASK_OPTIONS)
    options[CONF_API_MODE] = api_mode
    return MockConfigEntry(
        domain=DOMAIN,
        title="AI Task Runtime Acceptance",
        data={
            CONF_API_KEY: "sk-ai-task-acceptance",
            CONF_SKIP_AUTHENTICATION: True,
        },
        version=CONFIG_ENTRY_VERSION,
        subentries_data=[
            {
                "data": options,
                "subentry_type": "ai_task_data",
                "title": "AI Task Runtime",
                "unique_id": None,
            }
        ],
    )


async def _setup_entry(
    hass: HomeAssistant,
    outcomes: list[Outcome],
) -> tuple[MockConfigEntry, str, FakeClient]:
    """Load the real integration and replace only its provider SDK client."""
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    subentry = next(
        item
        for item in entry.subentries.values()
        if item.subentry_type == "ai_task_data"
    )
    entity_id = er.async_get(hass).async_get_entity_id(
        ai_task.DOMAIN, DOMAIN, subentry.subentry_id
    )
    assert entity_id is not None

    client = FakeClient(outcomes)
    entry.runtime_data = client
    return entry, entity_id, client


def _flatten_text(messages: list[Any]) -> str:
    """Return request text without depending on exact provider message nesting."""
    return str(messages)


@pytest.mark.asyncio
async def test_generate_data_uses_real_ha_runtime_and_does_not_leak_previous_task(
    hass: HomeAssistant,
) -> None:
    """Public HA AI Task calls reach the entity and each fresh task has fresh input."""
    _entry_obj, entity_id, client = await _setup_entry(
        hass, ["First result", "Second result"]
    )

    first = await ai_task.async_generate_data(
        hass,
        task_name="First Task",
        entity_id=entity_id,
        instructions="FIRST_TASK_MARKER",
    )
    second = await ai_task.async_generate_data(
        hass,
        task_name="Second Task",
        entity_id=entity_id,
        instructions="SECOND_TASK_MARKER",
    )

    assert first.data == "First result"
    assert second.data == "Second result"
    assert len(client.completions.calls) == 2
    first_messages = _flatten_text(client.completions.calls[0]["messages"])
    second_messages = _flatten_text(client.completions.calls[1]["messages"])
    assert "FIRST_TASK_MARKER" in first_messages
    assert "SECOND_TASK_MARKER" in second_messages
    assert "FIRST_TASK_MARKER" not in second_messages


@pytest.mark.asyncio
async def test_generate_data_structured_output_flows_through_home_assistant(
    hass: HomeAssistant,
) -> None:
    """HA's public task API returns parsed structured data and requests JSON schema."""
    _entry_obj, entity_id, client = await _setup_entry(
        hass, ['{"answer":"ready","count":2}']
    )
    structure = vol.Schema(
        {
            vol.Required("answer"): str,
            vol.Required("count"): int,
        }
    )

    result = await ai_task.async_generate_data(
        hass,
        task_name="Structured Runtime Task",
        entity_id=entity_id,
        instructions="Return the requested fields.",
        structure=structure,
    )

    assert result.data == {"answer": "ready", "count": 2}
    request = client.completions.calls[0]
    response_format = request["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True
    schema = response_format["json_schema"]["schema"]
    assert set(schema["properties"]) == {"answer", "count"}


@pytest.mark.asyncio
async def test_generate_data_image_attachment_reaches_provider_request(
    hass: HomeAssistant,
    tmp_path: Path,
) -> None:
    """HA resolves a local image attachment and the integration sends its bytes."""
    _entry_obj, entity_id, client = await _setup_entry(hass, ["Image inspected"])
    image_path = tmp_path / "task-image.png"
    image_path.write_bytes(b"deterministic-image-bytes")

    with patch(
        "homeassistant.components.media_source.async_resolve_media",
        return_value=media_source.PlayMedia(
            url="http://example.invalid/task-image.png",
            mime_type="image/png",
            path=image_path,
        ),
    ):
        result = await ai_task.async_generate_data(
            hass,
            task_name="Attachment Runtime Task",
            entity_id=entity_id,
            instructions="Inspect the attached image.",
            attachments=[
                {
                    "media_content_id": "media-source://local/task-image.png",
                    "media_content_type": "image/png",
                }
            ],
        )

    assert result.data == "Image inspected"
    user_message = next(
        message
        for message in reversed(client.completions.calls[0]["messages"])
        if message.get("role") == "user"
    )
    assert isinstance(user_message["content"], list)
    assert user_message["content"][0] == {
        "type": "text",
        "text": "Inspect the attached image.",
    }
    image_part = user_message["content"][1]
    assert image_part["type"] == "image_url"
    assert image_part["image_url"]["url"].startswith("data:image/png;base64,")


@pytest.mark.asyncio
async def test_caller_llm_tool_executes_with_the_original_home_assistant_context(
    hass: HomeAssistant,
) -> None:
    """Caller-supplied HA tools execute through the AI Task exchange with its Context."""

    def request_tool(kwargs: dict[str, Any]) -> list[Any]:
        tools = kwargs["tools"]
        assert len(tools) == 1
        alias = tools[0]["function"]["name"]
        return [
            _chunk(
                content=None,
                finish_reason="tool_calls",
                tool_calls=[
                    _tool_delta(0, alias, '{"value":"from-model"}', "call-1")
                ],
            )
        ]

    _entry_obj, entity_id, client = await _setup_entry(
        hass, [request_tool, "Tool round trip complete"]
    )
    tool = ContextProbeTool()
    caller_api = CallerAPI(hass=hass, id="runtime-probe", name="Runtime Probe")
    caller_api.tools = [tool]
    user = MockUser(id="ai-task-runtime-user", name="AI Task Runtime User")
    user.mock_policy({"entities": {"entity_ids": {entity_id: {"control": True}}}})
    user.add_to_hass(hass)
    context = Context(user_id=user.id)

    result = await ai_task.async_generate_data(
        hass,
        task_name="Tool Runtime Task",
        entity_id=entity_id,
        instructions="Use the available tool once.",
        llm_api=caller_api,
        context=context,
    )

    assert result.data == "Tool round trip complete"
    assert len(client.completions.calls) == 2
    assert len(tool.calls) == 1
    tool_input, llm_context = tool.calls[0]
    assert tool_input.tool_name == "context_probe"
    assert tool_input.tool_args == {"value": "from-model"}
    assert llm_context.context is context
    assert llm_context.context.user_id == user.id
    second_messages = _flatten_text(client.completions.calls[1]["messages"])
    assert "from-model" in second_messages


@pytest.mark.asyncio
async def test_ai_task_tool_budget_acknowledges_refused_overflow(
    hass: HomeAssistant,
) -> None:
    """Serial caller tools execute the allowed prefix and refuse the excess call."""

    def over_budget(kwargs: dict[str, Any]) -> list[Any]:
        alias = kwargs["tools"][0]["function"]["name"]
        count = DEFAULT_MAX_FUNCTION_CALLS_PER_CONVERSATION + 1
        return [
            _chunk(
                content=None,
                finish_reason="tool_calls",
                tool_calls=[
                    _tool_delta(
                        index,
                        alias,
                        '{"value":"budget"}',
                        f"call-{index}",
                    )
                    for index in range(count)
                ],
            )
        ]

    _entry_obj, entity_id, _client = await _setup_entry(hass, [over_budget, "Allowed calls completed"])
    tool = ContextProbeTool()
    caller_api = CallerAPI(hass=hass, id="budget-probe", name="Budget Probe")
    caller_api.tools = [tool]
    user = MockUser(id="budget-user", name="Budget User")
    user.mock_policy({"entities": {"entity_ids": {entity_id: {"control": True}}}})
    user.add_to_hass(hass)

    await ai_task.async_generate_data(
        hass,
        task_name="Budget Runtime Task",
        entity_id=entity_id,
        instructions="Attempt too many tool calls.",
        llm_api=caller_api,
        context=Context(user_id=user.id),
    )

    assert [call.id for call, _context in tool.calls] == [
        f"call-{index}" for index in range(DEFAULT_MAX_FUNCTION_CALLS_PER_CONVERSATION)
    ]
    assert all(
        call.tool_args == {"value": "budget"}
        and llm_context.context.user_id == user.id
        for call, llm_context in tool.calls
    )
    assert len(_client.completions.calls) == 2
    messages = _client.completions.calls[1]["messages"]
    refusals = [message for message in messages if message.get("role") == "tool" and "not executed: request Function Tool limit reached" in message.get("content", "")]
    assert len(refusals) == 1
    assert refusals[0]["tool_call_id"] == f"call-{DEFAULT_MAX_FUNCTION_CALLS_PER_CONVERSATION}"


@pytest.mark.asyncio
async def test_malformed_provider_stream_fails_through_public_ai_task_api(
    hass: HomeAssistant,
) -> None:
    """A partial provider stream cannot be mistaken for a successful AI Task result."""

    def incomplete_stream(_kwargs: dict[str, Any]) -> list[Any]:
        return [_chunk(content="partial", finish_reason=None)]

    _entry_obj, entity_id, _client = await _setup_entry(hass, [incomplete_stream])

    with pytest.raises(
        HomeAssistantError,
        match="stream ended before a terminal finish reason",
    ):
        await ai_task.async_generate_data(
            hass,
            task_name="Malformed Runtime Task",
            entity_id=entity_id,
            instructions="This provider response is intentionally incomplete.",
        )


@pytest.mark.parametrize("body", ['{}', '{"answer":"PRIVATE_TASK_INVALID"}'])
@pytest.mark.asyncio
async def test_invalid_structured_task_is_controlled_and_next_task_recovers(hass, body):
    _entry_obj, entity_id, client = await _setup_entry(hass, [body, '{"answer":2}'])
    # Use the schema engine of the installed Home Assistant release.
    from homeassistant.components.ai_task import task as task_module
    engine = getattr(task_module, "probatio", vol)
    structure = engine.Schema({engine.Required("answer"): int})
    with pytest.raises(HomeAssistantError, match="does not match") as error:
        await ai_task.async_generate_data(
            hass, task_name="Invalid task", entity_id=entity_id,
            instructions="Return an integer", structure=structure,
        )
    assert "PRIVATE_TASK_INVALID" not in str(error.value)
    result = await ai_task.async_generate_data(
        hass, task_name="Valid task", entity_id=entity_id,
        instructions="Return an integer", structure=structure,
    )
    assert result.data == {"answer": 2}
    assert len(client.completions.calls) == 2


class SnapshotProbeTool(ContextProbeTool):
    """Record the snapshot as well as the actual HA dispatch context."""

    def __init__(self):
        super().__init__()
        self.snapshots = []

    async def async_call(self, hass, tool_input, llm_context):
        self.snapshots.append(current_snapshot())
        return await super().async_call(hass, tool_input, llm_context)


class SnapshotAPI(CallerAPI):
    """A source that can be registered or supplied directly by an AI Task caller."""

    def __init__(self, hass, api_id, tools, *, caller_device=None, denied=False):
        super().__init__(hass=hass, id=api_id, name=api_id)
        self.tools = tools
        self.caller_device = caller_device
        self.caller_context = None
        self.denied = denied
        self.instances = []
        self.snapshots = []
        self.contexts = []
        self.prompt = "PARENT_API_PROMPT" if caller_device else "CHILD_API_PROMPT"
        self.serializer = lambda schema: llm.selector_serializer(schema)

    async def async_get_api_instance(self, llm_context):
        self.snapshots.append(current_snapshot())
        self.contexts.append(llm_context)
        if self.denied:
            raise HomeAssistantError("This source does not authorize the child device")
        # HA's public AI Task API has no device argument. A supplied API owns its
        # returned instance/context; retain that legitimate parent device context.
        if self.caller_device:
            llm_context = replace(
                llm_context, device_id=self.caller_device,
                context=self.caller_context or llm_context.context,
            )
        instance = llm.APIInstance(
            api=self,
            api_prompt=self.prompt,
            llm_context=llm_context,
            tools=list(self.tools),
            custom_serializer=self.serializer,
        )
        self.instances.append(instance)
        return instance


class NestedConversationTool(llm.Tool):
    """A caller-owned tool that invokes a conversation through public HA APIs."""

    name = "nested_conversation"
    description = "Invoke the child conversation."
    parameters = vol.Schema({})

    def __init__(self, invoke):
        self.invoke = invoke

    async def async_call(self, hass, tool_input, llm_context):
        return await self.invoke(llm_context)


def _snapshot_reference(api, tool):
    return {
        "type": "ha_llm",
        "source_type": "api",
        "api_id": api.id,
        "source_id": f"{type(tool).__module__}.{type(tool).__qualname__}",
        "tool_name": tool.name,
    }


@pytest.mark.parametrize("direct", [False, True], ids=["assist", "direct-service"])
@pytest.mark.parametrize(
    "access",
    ["absent-parent-reference", "matching-reference", "unavailable", "unauthorized"],
)
async def test_public_ai_task_nested_conversation_owns_its_ha_tool_snapshot(
    hass, hass_admin_user, direct, access
):
    from tests_real_ha.test_cross_feature_acceptance import _agent
    from tests_real_ha.test_request_rules_script_semantics import _local

    allowed = access in {"absent-parent-reference", "matching-reference"}
    child_tool, parent_tool = SnapshotProbeTool(), SnapshotProbeTool()
    child_api = SnapshotAPI(
        hass,
        "snapshot-child",
        [] if access == "unavailable" else [child_tool],
        denied=access == "unauthorized",
    )
    llm.async_register_api(hass, child_api)
    reference = _snapshot_reference(child_api, child_tool)
    saved = new_reference_tool(reference, set())
    child = await _agent(hass, **{CONF_FUNCTION_TOOLS: [saved]})
    await child._request_rules.async_create(
        _local(
            [
                {
                    "action": f"{DOMAIN}.{SERVICE_CALL_FUNCTION}",
                    "data": {
                        "function": saved["spec"]["name"],
                        "arguments": {"value": "child-rule"},
                    },
                },
            ],
            phrase="invoke child rule",
            success="Child rule succeeded",
            failure="Child tool denied",
        )
    )

    children, child_contexts, restored = [], [], []
    parent_snapshots = []
    parent_contents = {}
    parent_maps = []

    def child_model_round(kwargs):
        snapshot = current_snapshot()
        assert not snapshot.caller_provided
        names = {item["function"]["name"] for item in kwargs.get("tools", [])}
        assert (saved["spec"]["name"] in names) is allowed
        assert "PARENT_API_PROMPT" not in _flatten_text(kwargs["messages"])
        if not allowed:
            return [_chunk(content="Child model complete")]
        assert "CHILD_API_PROMPT" in _flatten_text(kwargs["messages"])
        return [
            _chunk(
                content=None,
                finish_reason="tool_calls",
                tool_calls=[
                    _tool_delta(
                        0,
                        saved["spec"]["name"],
                        '{"value":"child-model"}',
                        "child-call",
                    ),
                ],
            )
        ]

    child_client = FakeClient(
        [child_model_round, "Child model complete"] if allowed else [child_model_round]
    )
    child.entry.runtime_data = child_client

    async def invoke(llm_context):
        parent = current_snapshot()
        for text, device in [
            ("invoke child rule", child_device.id),
            ("invoke child model", model_device.id),
        ]:
            context = Context(
                user_id=llm_context.context.user_id, parent_id=llm_context.context.id
            )
            child_contexts.append(context)
            if direct:
                result = await hass.services.async_call(
                    DOMAIN,
                    SERVICE_PROCESS,
                    {
                        "agent_id": child.entity_id,
                        "text": text,
                        "language": "en",
                        "device_id": device,
                    },
                    blocking=True,
                    return_response=True,
                    context=context,
                )
                children.append((result["successful"], result["response"]))
            else:
                result = await conversation.async_converse(
                    hass,
                    text=text,
                    context=context,
                    language="en",
                    conversation_id=None,
                    agent_id=child.entry.entry_id,
                    device_id=device,
                )
                children.append(
                    (
                        result.response.error_code is None,
                        result.response.as_dict()["speech"]["plain"]["speech"],
                    )
                )
            restored.append(current_snapshot())
        return {"children": children, "parent_restored": current_snapshot() is parent}

    nested = NestedConversationTool(invoke)
    # Even with the same API ID, the supplied object is distinct from the
    # registered child source. Rediscovering parent tools would break this caller.
    parent_id = (
        "snapshot-parent-unregistered"
        if access == "absent-parent-reference"
        else child_api.id
    )
    parent_api = SnapshotAPI(
        hass, parent_id, [nested, parent_tool], caller_device="pending"
    )
    nested_alias = new_reference_tool(_snapshot_reference(parent_api, nested), set())[
        "spec"
    ]["name"]
    after_alias = new_reference_tool(
        _snapshot_reference(parent_api, parent_tool), set()
    )["spec"]["name"]

    def parent_round(alias=None):
        def respond(kwargs):
            snapshot = current_snapshot()
            parent_snapshots.append(snapshot)
            parent_maps.append(snapshot.tools)
            if not parent_contents:
                parent_contents.update(snapshot.tools)
            assert snapshot.caller_provided
            assert "PARENT_API_PROMPT" in _flatten_text(kwargs["messages"])
            assert "CHILD_API_PROMPT" not in _flatten_text(kwargs["messages"])
            if alias is None:
                return [_chunk(content="Parent complete")]
            assert alias in {item["function"]["name"] for item in kwargs["tools"]}
            arguments = "{}" if alias == nested_alias else '{"value":"parent-after"}'
            return [
                _chunk(
                    content=None,
                    finish_reason="tool_calls",
                    tool_calls=[
                        _tool_delta(
                            0,
                            alias,
                            arguments,
                            "nested-call" if alias == nested_alias else "after-call",
                        ),
                    ],
                )
            ]

        return respond

    parent_entry, task_entity, parent_client = await _setup_entry(
        hass,
        [parent_round(nested_alias), parent_round(after_alias), parent_round()],
    )
    registry = dr.async_get(hass)
    child_device, model_device = [
        registry.async_get_or_create(
            config_entry_id=child.entry.entry_id, identifiers={(DOMAIN, name)}
        )
        for name in ("snapshot-child-rule", "snapshot-child-model")
    ]
    parent_device = registry.async_get_or_create(
        config_entry_id=parent_entry.entry_id,
        identifiers={(DOMAIN, "snapshot-parent")},
    )
    parent_api.caller_device = parent_device.id
    assert all(api is not parent_api for api in llm.async_get_apis(hass))
    parent_context = Context(user_id=hass_admin_user.id)
    parent_api.caller_context = parent_context
    task_context = (
        {"context": parent_context}
        if "context" in inspect.signature(ai_task.async_generate_data).parameters
        else {}
    )
    outer = ToolSnapshot(
        caller_provided=True, caller_prompt="outer", unavailable_sources=["retain"]
    )
    with tool_snapshot_scope(outer):
        result = await ai_task.async_generate_data(
            hass,
            task_name="Nested snapshot task",
            entity_id=task_entity,
            instructions="Invoke the child, then the other supplied tool.",
            llm_api=parent_api,
            **task_context,
        )
        assert current_snapshot() is outer
        assert outer.caller_prompt == "outer" and outer.unavailable_sources == [
            "retain"
        ]

    assert result.data == "Parent complete"
    assert children == [
        (allowed, "Child rule succeeded" if allowed else "Child tool denied"),
        (True, "Child model complete"),
    ]
    assert len(parent_snapshots) == 3 and len(restored) == 2
    parent_snapshot = parent_snapshots[0]
    assert all(
        snapshot is parent_snapshot for snapshot in [*parent_snapshots, *restored]
    )
    assert parent_snapshot.caller_provided
    assert parent_snapshot.unavailable_sources == [] and parent_snapshot.caller_prompt == ""
    assert all(mapping is parent_snapshot.tools for mapping in parent_maps)
    assert set(parent_snapshot.tools) == set(parent_contents)
    assert all(
        parent_snapshot.tools[key] is live for key, live in parent_contents.items()
    )
    assert (reference_key(reference) in parent_snapshot.tools) is (
        access != "absent-parent-reference"
    )
    assert len(parent_api.instances) == 1
    instance = parent_api.instances[0]
    if task_context:
        assert parent_api.contexts[0].context is parent_context
    assert (
        instance.api is parent_api
        and instance.custom_serializer is parent_api.serializer
    )
    assert instance.api_prompt == "PARENT_API_PROMPT"
    assert instance.llm_context.context is parent_context
    assert instance.llm_context.device_id == parent_device.id
    assert instance.tools == [nested, parent_tool]
    assert all(live.instance is instance for live in parent_snapshot.tools.values())
    assert len(parent_tool.calls) == 1
    assert parent_tool.calls[0][0].tool_args == {"value": "parent-after"}
    assert parent_tool.calls[0][1] is instance.llm_context
    assert parent_tool.snapshots == [parent_snapshot]
    assert len(parent_client.completions.calls) == 3
    assert "parent-after" in _flatten_text(
        parent_client.completions.calls[-1]["messages"]
    )
    assert [call[0].tool_args["value"] for call in child_tool.calls] == (
        ["child-rule", "child-model"] if allowed else []
    )
    assert len(child_client.completions.calls) == (2 if allowed else 1)
    assert child_api.snapshots[0] is not child_api.snapshots[1]
    assert all(
        not snapshot.caller_provided and snapshot is not parent_snapshot
        for snapshot in child_api.snapshots
    )
    if allowed:
        for index, (_, context) in enumerate(child_tool.calls):
            assert context.context is child_contexts[index]
            assert context.context.user_id == parent_context.user_id
            assert context.device_id == (
                child_device.id if index == 0 else model_device.id
            )
            assert context is not instance.llm_context
        # First discovery is the local Rule; the next starts a separate model
        # invocation. Later model dispatch owns its normal projected snapshot.
        assert child_tool.snapshots[0].tools == {}
        assert reference_key(reference) in child_tool.snapshots[1].tools
