"""Manual-only full live OpenAI journey through a genuine Home Assistant process."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

import yaml

DOMAIN = "extended_openai_conversation_responses"
_CHILD_ENV = "LIVE_OPENAI_E2E_CHILD"
_CONFIG_DIR_ENV = "LIVE_OPENAI_E2E_CONFIG_DIR"
_MODEL_ENV = "LIVE_OPENAI_E2E_MODEL"
_API_KEY_ENV = "OPENAI_API_KEY"
_REPORT_ENV = "LIVE_OPENAI_E2E_REPORT"

_TOOL_NAME = "fetch_live_acceptance_marker"
_TOOL_MARKER = "EOAI_LIVE_TOOL_RESULT_7F3A"
_KNOWLEDGE_MARKER = "EOAI_LIVE_KNOWLEDGE_CODE_K9Q2"

if not os.environ.get(_CHILD_ENV):
    import pytest

    pytestmark = pytest.mark.skipif(
        not os.environ.get(_API_KEY_ENV) or not os.environ.get(_MODEL_ENV),
        reason="manual live OpenAI acceptance only",
    )


def _conversation_subentry(entry: Any) -> Any:
    return next(
        item
        for item in entry.subentries.values()
        if item.subentry_type == "conversation"
    )


def _raw_client(agent: Any) -> Any:
    client = agent._client
    while hasattr(client, "_delegate"):
        client = client._delegate
    return client


def _decode_request(request: Any) -> dict[str, Any]:
    try:
        value = json.loads(request.content)
    except (json.JSONDecodeError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


class ProviderTrace:
    """Observe real OpenAI traffic while forwarding every request unchanged."""

    def __init__(self, agent: Any) -> None:
        self.agent = agent
        self.requests: list[dict[str, Any]] = []
        self.tool_executions: list[dict[str, Any]] = []
        self._client = _raw_client(agent)._client
        self._original_send = self._client.send
        self._original_execute = agent._execute_function_tool

    async def send(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        body = _decode_request(request)
        self.requests.append(
            {
                "method": request.method,
                "url": str(request.url),
                "path": request.url.path,
                "body": body,
            }
        )
        return await self._original_send(request, *args, **kwargs)

    async def execute(self, function_tool: Any, tool_input: Any, *args: Any, **kwargs: Any) -> Any:
        self.tool_executions.append(
            {
                "name": tool_input.tool_name,
                "arguments": deepcopy(tool_input.tool_args),
            }
        )
        return await self._original_execute(
            function_tool,
            tool_input,
            *args,
            **kwargs,
        )

    def install(self) -> None:
        self._client.send = self.send
        self.agent._execute_function_tool = self.execute

    def restore(self) -> None:
        self._client.send = self._original_send
        self.agent._execute_function_tool = self._original_execute


def _tool_names(body: dict[str, Any]) -> set[str]:
    tools = body.get("tools", [])
    names: set[str] = set()
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        if tool.get("type") == "function" and isinstance(tool.get("name"), str):
            names.add(tool["name"])
        function = tool.get("function")
        if isinstance(function, dict) and isinstance(function.get("name"), str):
            names.add(function["name"])
    return names


def _serialized_tool_call(body: dict[str, Any], name: str) -> bool:
    for item in body.get("input", []):
        if isinstance(item, dict) and item.get("type") == "function_call":
            if item.get("name") == name:
                return True
    for item in body.get("messages", []):
        if not isinstance(item, dict) or item.get("role") != "assistant":
            continue
        for call in item.get("tool_calls", []):
            if call.get("function", {}).get("name") == name:
                return True
    return False


def _serialized_tool_result_contains(body: dict[str, Any], marker: str) -> bool:
    serialized = json.dumps(body, ensure_ascii=False)
    if marker not in serialized:
        return False
    for item in body.get("input", []):
        if isinstance(item, dict) and item.get("type") == "function_call_output":
            if marker in json.dumps(item, ensure_ascii=False):
                return True
    for item in body.get("messages", []):
        if isinstance(item, dict) and item.get("role") == "tool":
            if marker in json.dumps(item, ensure_ascii=False):
                return True
    return False


async def _create_entry(hass: Any) -> Any:
    from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
    from homeassistant.const import CONF_API_KEY, CONF_NAME
    from homeassistant.data_entry_flow import FlowResultType
    from custom_components.extended_openai_conversation_responses import const

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"api_provider": "openai"},
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Live OpenAI End-to-End",
            CONF_API_KEY: os.environ[_API_KEY_ENV],
            const.CONF_SKIP_AUTHENTICATION: True,
            const.CONF_API_PROVIDER: "openai",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    entry = result["result"]
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    return entry


async def _configure_agent(hass: Any, entry: Any) -> tuple[Any, str, str | None]:
    from homeassistant.components import conversation
    from custom_components.extended_openai_conversation_responses import (
        agent_config,
        const,
    )
    from custom_components.extended_openai_conversation_responses.model_capabilities import (
        capability_allowed,
        get_model_capabilities,
        reasoning_efforts_for_api,
    )

    subentry = _conversation_subentry(entry)
    data = dict(subentry.data)
    model = os.environ[_MODEL_ENV]
    capabilities = get_model_capabilities(model)
    profile = capabilities.get("recommended_profile", {})
    candidates: list[tuple[str, str | None]] = []
    preferred_api = profile.get("api")
    preferred_effort = profile.get("reasoning_effort")
    if isinstance(preferred_api, str):
        candidates.append((preferred_api, preferred_effort))
    for api_mode in ("responses", "chat_completions"):
        if not capabilities.get("api", {}).get(api_mode):
            continue
        efforts = reasoning_efforts_for_api(model, api_mode) or [None]
        candidates.extend((api_mode, effort) for effort in efforts)

    selected: tuple[str, str | None] | None = None
    for api_mode, effort in candidates:
        if not capabilities.get("api", {}).get(api_mode):
            continue
        if capability_allowed(model, "function", api_mode, effort=effort):
            selected = (api_mode, effort)
            break
    assert selected is not None, f"{model} has no function-capable EOAI API profile"
    api_mode, effort = selected
    tool = {
        "spec": {
            "name": _TOOL_NAME,
            "description": (
                "Return the deterministic live acceptance marker. "
                "Use this when explicitly asked for the live acceptance marker."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
        "function": {
            "type": "template",
            "value_template": _TOOL_MARKER,
        },
        "enabled": True,
    }
    updates = {
        const.CONF_API_MODE: api_mode,
        const.CONF_CHAT_MODEL: model,
        const.CONF_MAX_TOKENS: 192,
        const.CONF_FUNCTION_TOOLS: yaml.safe_dump(
            [tool],
            sort_keys=False,
            allow_unicode=True,
        ),
        const.CONF_KNOWLEDGE_ENABLED: True,
        const.CONF_MEMORY_MODE: "manual",
        const.CONF_PROMPT: (
            "You are running a bounded integration acceptance test. "
            "Follow explicit requests to use available EOAI tools. "
            "Keep final answers concise and preserve acceptance markers exactly."
        ),
    }
    data.pop(const.CONF_REASONING_EFFORT, None)
    if effort is not None:
        updates[const.CONF_REASONING_EFFORT] = effort
    merged = agent_config.merge_agent_config(data, updates)
    hass.config_entries.async_update_subentry(entry, subentry, data=merged)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert agent is not None
    return agent, api_mode, effort


async def _converse(hass: Any, agent: Any, text: str) -> Any:
    from homeassistant.components import conversation
    from homeassistant.core import Context

    return await conversation.async_converse(
        hass=hass,
        text=text,
        conversation_id=None,
        context=Context(),
        language="en",
        agent_id=agent.entry.entry_id,
    )


async def _journey_tool_continuation(hass: Any, agent: Any) -> dict[str, Any]:
    trace = ProviderTrace(agent)
    trace.install()
    try:
        result = await _converse(
            hass,
            agent,
            (
                f"Call the {_TOOL_NAME} tool exactly once now. "
                "Do not answer before using it. After the tool result returns, "
                "reply with one short sentence that includes the returned marker exactly."
            ),
        )
    finally:
        trace.restore()

    assert result.response.error_code is None
    speech = result.response.as_dict()["speech"]["plain"]["speech"]
    assert _TOOL_MARKER in speech
    assert len(trace.requests) >= 2
    assert _TOOL_NAME in _tool_names(trace.requests[0]["body"])
    assert any(
        _serialized_tool_call(request["body"], _TOOL_NAME)
        for request in trace.requests[1:]
    )
    assert trace.tool_executions == [{"name": _TOOL_NAME, "arguments": {}}]
    continuation = next(
        request
        for request in trace.requests[1:]
        if _serialized_tool_result_contains(request["body"], _TOOL_MARKER)
    )
    assert continuation["path"] in {"/v1/responses", "/v1/chat/completions"}

    return {
        "provider_calls": len(trace.requests),
        "paths": [request["path"] for request in trace.requests],
        "tool_name": _TOOL_NAME,
        "tool_executions": len(trace.tool_executions),
        "continuation_contains_tool_result": True,
        "final_response_contains_marker": True,
    }


async def _journey_knowledge(hass: Any, agent: Any) -> dict[str, Any]:
    source = await agent._knowledge.async_create(
        "Live acceptance retained reference",
        "Deterministic reference used only by the manual live acceptance journey.",
        f"The live retained acceptance code is {_KNOWLEDGE_MARKER}.",
    )

    trace = ProviderTrace(agent)
    trace.install()
    try:
        result = await _converse(
            hass,
            agent,
            (
                "Use knowledge_search to find the live retained acceptance code. "
                "Do not guess it and do not answer before using Knowledge. "
                "Reply with one short sentence containing the exact code you found."
            ),
        )
    finally:
        trace.restore()

    assert result.response.error_code is None
    speech = result.response.as_dict()["speech"]["plain"]["speech"]
    assert _KNOWLEDGE_MARKER in speech
    assert len(trace.requests) >= 2
    assert "knowledge_search" in _tool_names(trace.requests[0]["body"])
    assert any(
        item["name"] == "knowledge_search"
        for item in trace.tool_executions
    )
    assert any(
        _serialized_tool_result_contains(request["body"], _KNOWLEDGE_MARKER)
        for request in trace.requests[1:]
    )

    loaded = await agent._knowledge.async_get(source.source_id)
    assert _KNOWLEDGE_MARKER in loaded.content

    return {
        "provider_calls": len(trace.requests),
        "paths": [request["path"] for request in trace.requests],
        "knowledge_source_id": source.source_id,
        "knowledge_tool_executed": True,
        "continuation_contains_retained_marker": True,
        "final_response_contains_marker": True,
    }


async def _child_main() -> None:
    from homeassistant import bootstrap, runner
    from homeassistant.helpers import recorder as recorder_helper

    config_dir = Path(os.environ[_CONFIG_DIR_ENV]).resolve()
    sys.path.insert(0, str(config_dir))

    hass = await bootstrap.async_setup_hass(
        runner.RuntimeConfig(config_dir=str(config_dir), skip_pip=False)
    )
    assert hass is not None
    if recorder_helper.DATA_RECORDER not in hass.data:
        recorder_helper.async_initialize_recorder(hass)
    await hass.async_start()

    try:
        entry = await _create_entry(hass)
        agent, api_mode, effort = await _configure_agent(hass, entry)

        tool = await _journey_tool_continuation(hass, agent)
        knowledge = await _journey_knowledge(hass, agent)

        report = {
            "model": os.environ[_MODEL_ENV],
            "api_mode": api_mode,
            "reasoning_effort": effort,
            "entry_id": entry.entry_id,
            "subentry_id": agent.subentry.subentry_id,
            "journey_a": tool,
            "journey_b": knowledge,
            "total_provider_calls": tool["provider_calls"] + knowledge["provider_calls"],
        }
        Path(os.environ[_REPORT_ENV]).write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    finally:
        await hass.async_stop()


def test_full_live_openai_eoai_journey(
    socket_enabled: Any,
    tmp_path: Path,
) -> None:
    """Boot HA in a fresh process and exercise two bounded real-provider journeys."""
    del socket_enabled
    repo_root = Path(__file__).resolve().parents[1]
    config_dir = tmp_path / "ha-config"
    destination = config_dir / "custom_components" / DOMAIN
    destination.parent.mkdir(parents=True)
    shutil.copytree(repo_root / "custom_components" / DOMAIN, destination)
    (config_dir / "configuration.yaml").write_text(
        "homeassistant:\n"
        "  name: Live OpenAI End-to-End\n"
        "recorder:\n",
        encoding="utf-8",
    )

    report_path = tmp_path / "live-openai-e2e-report.json"
    env = os.environ.copy()
    env[_CHILD_ENV] = "1"
    env[_CONFIG_DIR_ENV] = str(config_dir)
    env[_REPORT_ENV] = str(report_path)
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = os.pathsep.join(
        [str(config_dir), str(repo_root), *(existing.split(os.pathsep) if existing else [])]
    )

    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve())],
        cwd=config_dir,
        env=env,
        text=True,
        capture_output=True,
        timeout=240,
        check=False,
    )
    assert result.returncode == 0, (
        "live OpenAI EOAI child failed\n"
        f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    )
    assert report_path.is_file()

    final_report = Path(os.environ.get(_REPORT_ENV, str(report_path)))
    final_report.parent.mkdir(parents=True, exist_ok=True)
    if final_report.resolve() != report_path.resolve():
        shutil.copyfile(report_path, final_report)


if __name__ == "__main__" and os.environ.get(_CHILD_ENV):
    asyncio.run(_child_main())
