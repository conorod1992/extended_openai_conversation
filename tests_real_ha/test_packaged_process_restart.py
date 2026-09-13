"""Process-level packaged-install and cold-restart acceptance journey."""

from __future__ import annotations

import asyncio
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

DOMAIN = "extended_openai_conversation_responses"
_CHILD_PHASE = "PACKAGED_PROCESS_ACCEPTANCE_PHASE"
_ENTRY_MARKER = "packaged-process-entry.json"

pytestmark = pytest.mark.skipif(
    not os.environ.get("RELEASE_COMPONENT_DIR"),
    reason="only enabled for packaged-release smoke runs",
)


def _chat_sse_text(text: str) -> bytes:
    chunk = {
        "id": "chatcmpl-packaged-process",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "gpt-5.6",
        "choices": [
            {
                "index": 0,
                "delta": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
    }
    return f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n".encode()


def _response_object(text: str) -> dict[str, Any]:
    item = {
        "id": "msg-packaged-process",
        "type": "message",
        "role": "assistant",
        "status": "completed",
        "content": [
            {
                "type": "output_text",
                "text": text,
                "annotations": [],
                "logprobs": [],
            }
        ],
    }
    return {
        "id": "resp-packaged-process",
        "object": "response",
        "created_at": 0,
        "status": "completed",
        "error": None,
        "incomplete_details": None,
        "instructions": None,
        "max_output_tokens": 500,
        "model": "gpt-5.6",
        "output": [item],
        "parallel_tool_calls": True,
        "previous_response_id": None,
        "reasoning": {"effort": "medium", "summary": None},
        "store": False,
        "temperature": None,
        "text": {"format": {"type": "text"}},
        "tool_choice": "auto",
        "tools": [],
        "top_p": None,
        "truncation": "disabled",
        "usage": None,
    }


def _responses_sse_text(text: str) -> bytes:
    response = _response_object(text)
    item = response["output"][0]
    events = [
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {**item, "status": "in_progress", "content": []},
            "sequence_number": 0,
        },
        {
            "type": "response.output_text.delta",
            "content_index": 0,
            "delta": text,
            "item_id": item["id"],
            "logprobs": [],
            "output_index": 0,
            "sequence_number": 1,
        },
        {
            "type": "response.output_item.done",
            "output_index": 0,
            "item": item,
            "sequence_number": 2,
        },
        {
            "type": "response.completed",
            "response": response,
            "sequence_number": 3,
        },
    ]
    return "".join(f"data: {json.dumps(event)}\n\n" for event in events).encode()


class _TextWire:
    """Return one SDK-compatible text response without external network access."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.requests: list[str] = []

    async def send(
        self, request: httpx.Request, *args: Any, **kwargs: Any
    ) -> httpx.Response:
        del args, kwargs
        self.requests.append(request.url.path)
        if request.url.path == "/v1/chat/completions":
            content = _chat_sse_text(self.text)
        elif request.url.path == "/v1/responses":
            content = _responses_sse_text(self.text)
        else:
            raise AssertionError(f"Unexpected provider path: {request.url.path}")
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=content,
            request=request,
        )


def _raw_client(agent: Any) -> Any:
    client = agent._client
    while hasattr(client, "_delegate"):
        client = client._delegate
    return client


async def _exercise_conversation(hass: Any, entry_id: str, expected: str) -> None:
    from homeassistant.components import conversation
    from homeassistant.core import Context

    agent = conversation.async_get_agent(hass, entry_id)
    assert agent is not None

    wire = _TextWire(expected)
    raw_client = _raw_client(agent)
    with patch.object(raw_client._client, "send", wire.send):
        result = await conversation.async_converse(
            hass=hass,
            text="Confirm the packaged process acceptance test is running",
            conversation_id=None,
            context=Context(),
            language="en",
            agent_id=entry_id,
        )

    assert result.response.error_code is None
    assert result.response.as_dict()["speech"]["plain"]["speech"] == expected
    assert wire.requests in (["/v1/responses"], ["/v1/chat/completions"])


def _assert_packaged_module(config_dir: Path) -> None:
    module = importlib.import_module(f"custom_components.{DOMAIN}")
    module_path = Path(module.__file__).resolve()
    packaged_root = (config_dir / "custom_components" / DOMAIN).resolve()
    assert packaged_root in module_path.parents, (
        "child Home Assistant process imported the integration outside the staged "
        f"release payload: {module_path}"
    )


async def _first_boot(config_dir: Path) -> None:
    from homeassistant import bootstrap, runner
    from homeassistant.config_entries import ConfigEntryState, SOURCE_USER
    from homeassistant.const import CONF_API_KEY, CONF_NAME
    from homeassistant.data_entry_flow import FlowResultType

    sys.path.insert(0, str(config_dir))
    hass = await bootstrap.async_setup_hass(
        runner.RuntimeConfig(config_dir=str(config_dir), skip_pip=True)
    )
    assert hass is not None
    await hass.async_start()

    try:
        config_flow = importlib.import_module(f"custom_components.{DOMAIN}.config_flow")
        const = importlib.import_module(f"custom_components.{DOMAIN}.const")
        _assert_packaged_module(config_dir)

        authenticate = AsyncMock(return_value=object())
        with patch.object(config_flow, "get_authenticated_client", authenticate):
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": SOURCE_USER}
            )
            assert result["type"] is FlowResultType.FORM
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {
                    CONF_NAME: "Packaged Process Acceptance",
                    CONF_API_KEY: "sk-packaged-process",
                    const.CONF_BASE_URL: const.DEFAULT_CONF_BASE_URL,
                    const.CONF_SKIP_AUTHENTICATION: True,
                    const.CONF_API_PROVIDER: "openai",
                },
            )

        assert result["type"] is FlowResultType.CREATE_ENTRY
        entry = result["result"]
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED
        assert hass.services.has_service(DOMAIN, const.SERVICE_PROCESS)

        await _exercise_conversation(hass, entry.entry_id, "First process is healthy.")
        (config_dir / _ENTRY_MARKER).write_text(
            json.dumps({"entry_id": entry.entry_id}), encoding="utf-8"
        )
    finally:
        await hass.async_stop()


async def _second_boot(config_dir: Path) -> None:
    from homeassistant import bootstrap, runner
    from homeassistant.config_entries import ConfigEntryState

    sys.path.insert(0, str(config_dir))
    marker = json.loads((config_dir / _ENTRY_MARKER).read_text(encoding="utf-8"))

    hass = await bootstrap.async_setup_hass(
        runner.RuntimeConfig(config_dir=str(config_dir), skip_pip=True)
    )
    assert hass is not None
    await hass.async_start()

    try:
        const = importlib.import_module(f"custom_components.{DOMAIN}.const")
        _assert_packaged_module(config_dir)

        entries = hass.config_entries.async_entries(DOMAIN)
        assert len(entries) == 1
        entry = entries[0]
        assert entry.entry_id == marker["entry_id"]
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED
        assert hass.services.has_service(DOMAIN, const.SERVICE_PROCESS)

        await _exercise_conversation(hass, entry.entry_id, "Cold restart is healthy.")
    finally:
        await hass.async_stop()


async def _child_main() -> None:
    config_dir = Path(os.environ["PACKAGED_PROCESS_CONFIG_DIR"]).resolve()
    phase = os.environ[_CHILD_PHASE]
    if phase == "first":
        await _first_boot(config_dir)
    elif phase == "second":
        await _second_boot(config_dir)
    else:
        raise AssertionError(f"Unknown packaged-process phase: {phase}")


def _run_child(config_dir: Path, phase: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env[_CHILD_PHASE] = phase
    env["PACKAGED_PROCESS_CONFIG_DIR"] = str(config_dir)
    return subprocess.run(
        [sys.executable, str(Path(__file__).resolve())],
        cwd=config_dir,
        env=env,
        text=True,
        capture_output=True,
        timeout=90,
        check=False,
    )


def test_packaged_release_survives_a_true_process_restart(tmp_path: Path) -> None:
    """Run a packaged integration through two independent HA Python processes."""
    source = Path(os.environ["RELEASE_COMPONENT_DIR"]).resolve()
    expected_version = os.environ["RELEASE_EXPECTED_VERSION"]
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["domain"] == DOMAIN
    assert manifest["version"] == expected_version

    config_dir = tmp_path / "ha-config"
    destination = config_dir / "custom_components" / DOMAIN
    destination.parent.mkdir(parents=True)
    shutil.copytree(source, destination)
    (config_dir / "configuration.yaml").write_text(
        "homeassistant:\n  name: Packaged Process Acceptance\n",
        encoding="utf-8",
    )

    first = _run_child(config_dir, "first")
    assert first.returncode == 0, (
        "first packaged HA process failed\n"
        f"stdout:\n{first.stdout}\n\nstderr:\n{first.stderr}"
    )

    second = _run_child(config_dir, "second")
    assert second.returncode == 0, (
        "cold-restarted packaged HA process failed\n"
        f"stdout:\n{second.stdout}\n\nstderr:\n{second.stderr}"
    )


if __name__ == "__main__" and os.environ.get(_CHILD_PHASE):
    asyncio.run(_child_main())
