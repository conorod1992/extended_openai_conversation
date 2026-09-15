"""Real upgrade acceptance for custom Function Tools accepted by a published release."""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from aiohttp.test_utils import TestClient, TestServer
import pytest
import yaml

DOMAIN = "extended_openai_conversation_responses"
_FROM_COMPONENT_ENV = "UPGRADE_FROM_COMPONENT_DIR"
_TO_COMPONENT_ENV = "UPGRADE_TO_COMPONENT_DIR"
_FROM_VERSION_ENV = "UPGRADE_FROM_VERSION"
_TO_VERSION_ENV = "UPGRADE_TO_VERSION"
_CHILD_PHASE_ENV = "UPGRADE_CUSTOM_TOOL_CHILD_PHASE"
_CONFIG_DIR_ENV = "UPGRADE_CUSTOM_TOOL_CONFIG_DIR"
_STATE_FILE = "upgrade-custom-tool-state.json"
_CUSTOM_TOOL_NAME = "upgrade_phone_tool"

pytestmark = pytest.mark.skipif(
    not os.environ.get(_FROM_COMPONENT_ENV) or not os.environ.get(_TO_COMPONENT_ENV),
    reason="requires released and candidate component payloads",
)


class _ProviderRequestCaptured(RuntimeError):
    """Stop a real conversation exactly at the outbound provider boundary."""


class _CaptureEndpoint:
    def __init__(self, calls: list[dict[str, Any]]) -> None:
        self.calls = calls

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        raise _ProviderRequestCaptured


class _CaptureClient:
    def __init__(self, calls: list[dict[str, Any]]) -> None:
        endpoint = _CaptureEndpoint(calls)
        self.responses = endpoint
        self.chat = SimpleNamespace(completions=endpoint)


def _manifest(component_dir: Path) -> dict[str, Any]:
    return json.loads((component_dir / "manifest.json").read_text(encoding="utf-8"))


def _component_digest(component_dir: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(component_dir.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        digest.update(str(path.relative_to(component_dir)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _install_component(source: Path, config_dir: Path) -> None:
    destination = config_dir / "custom_components" / DOMAIN
    if destination.exists():
        shutil.rmtree(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination)


def _ensure_process_config(config_dir: Path) -> None:
    config_path = config_dir / "configuration.yaml"
    text = config_path.read_text(encoding="utf-8")
    if "\nrecorder:" not in text and not text.startswith("recorder:"):
        if text and not text.endswith("\n"):
            text += "\n"
        config_path.write_text(f"{text}recorder:\n", encoding="utf-8")


def _run_child(config_dir: Path, phase: str) -> subprocess.CompletedProcess[str]:
    _ensure_process_config(config_dir)
    env = os.environ.copy()
    env[_CHILD_PHASE_ENV] = phase
    env[_CONFIG_DIR_ENV] = str(config_dir)
    return subprocess.run(
        [sys.executable, str(Path(__file__).resolve())],
        cwd=config_dir,
        env=env,
        text=True,
        capture_output=True,
        timeout=150,
        check=False,
    )


def _assert_child_ok(result: subprocess.CompletedProcess[str], phase: str) -> None:
    assert result.returncode == 0, (
        f"custom Function Tool upgrade phase {phase!r} failed\n"
        f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    )


def _conversation_subentry(entry: Any) -> Any:
    return next(
        subentry
        for subentry in entry.subentries.values()
        if subentry.subentry_type == "conversation"
    )


def _custom_tool(description: str = "Published-release custom phone tool") -> dict[str, Any]:
    return {
        "spec": {
            "name": _CUSTOM_TOOL_NAME,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {
                    "phone": {
                        "type": "string",
                        "enum": ["mobile", "home"],
                        "enumNames": ["Mobile phone", "Home phone"],
                    }
                },
                "required": ["phone"],
            },
        },
        "function": {"type": "script", "sequence": []},
    }


def _tools(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, str):
        value = yaml.safe_load(value)
    assert isinstance(value, list)
    return [tool for tool in value if isinstance(tool, dict)]


def _find_custom_tool(value: Any) -> dict[str, Any]:
    for tool in _tools(value):
        spec = tool.get("spec")
        if isinstance(spec, dict) and spec.get("name") == _CUSTOM_TOOL_NAME:
            return tool
    raise AssertionError(f"{_CUSTOM_TOOL_NAME} was not found")


def _assert_phone_schema(tool: dict[str, Any]) -> None:
    phone = tool["spec"]["parameters"]["properties"]["phone"]
    assert phone["enum"] == ["mobile", "home"]
    assert phone["enumNames"] == ["Mobile phone", "Home phone"]


def _provider_function_tool(call: dict[str, Any]) -> dict[str, Any]:
    for tool in call.get("tools", []):
        if not isinstance(tool, dict):
            continue
        if tool.get("type") == "function" and tool.get("name") == _CUSTOM_TOOL_NAME:
            return tool
        function = tool.get("function")
        if isinstance(function, dict) and function.get("name") == _CUSTOM_TOOL_NAME:
            return function
    raise AssertionError(f"provider request omitted {_CUSTOM_TOOL_NAME}")


async def _create_released_entry(hass: Any) -> Any:
    from homeassistant.config_entries import ConfigEntryState, SOURCE_USER
    from homeassistant.const import CONF_API_KEY, CONF_NAME
    from homeassistant.data_entry_flow import FlowResultType

    config_flow = importlib.import_module(f"custom_components.{DOMAIN}.config_flow")
    const = importlib.import_module(f"custom_components.{DOMAIN}.const")

    authenticate = AsyncMock(return_value=object())
    with patch.object(config_flow, "get_authenticated_client", authenticate):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        assert result["type"] is FlowResultType.FORM
        user_input: dict[str, Any] = {
            CONF_NAME: "Published Release Custom Tool Upgrade",
            CONF_API_KEY: "sk-release-upgrade-custom-tool",
            const.CONF_SKIP_AUTHENTICATION: True,
            const.CONF_API_PROVIDER: "openai",
        }
        if hasattr(const, "CONF_BASE_URL") and hasattr(const, "DEFAULT_CONF_BASE_URL"):
            user_input[const.CONF_BASE_URL] = const.DEFAULT_CONF_BASE_URL
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    entry = result["result"]
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    return entry


async def _management_ws_calls(
    hass: Any, messages: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Send real authenticated commands through HA's WebSocket API."""
    from homeassistant.setup import async_setup_component
    from pytest_homeassistant_custom_component.common import CLIENT_ID, MockUser

    assert await async_setup_component(hass, "websocket_api", {})
    admin = MockUser(
        id=f"upgrade-custom-tool-{uuid4().hex}",
        name="Upgrade Custom Tool Admin",
        is_owner=True,
    )
    admin.add_to_hass(hass)
    refresh_token = await hass.auth.async_create_refresh_token(admin, CLIENT_ID)
    access_token = hass.auth.async_create_access_token(refresh_token)

    results: list[dict[str, Any]] = []
    async with TestClient(TestServer(hass.http.app)) as client:
        socket = await client.ws_connect("/api/websocket")
        auth_required = await socket.receive_json()
        assert auth_required["type"] == "auth_required"
        await socket.send_json({"type": "auth", "access_token": access_token})
        auth_ok = await socket.receive_json()
        assert auth_ok["type"] == "auth_ok"

        for message_id, message in enumerate(messages, start=1):
            await socket.send_json(
                {
                    "id": message_id,
                    "type": f"{DOMAIN}/management",
                    **message,
                }
            )
            response = await socket.receive_json()
            assert response.get("id") == message_id
            assert response.get("success") is True, response.get("error")
            result = response.get("result")
            assert isinstance(result, dict)
            results.append(result)
        await socket.close()
    return results


async def _capture_real_provider_request(hass: Any, entry: Any) -> dict[str, Any]:
    """Run the ordinary conversation pipeline and stop at the SDK wire seam."""
    from homeassistant.components import conversation
    from homeassistant.core import Context

    calls: list[dict[str, Any]] = []
    original_client = entry.runtime_data
    entry.runtime_data = _CaptureClient(calls)
    try:
        try:
            await conversation.async_converse(
                hass=hass,
                text="Use the upgrade phone tool with my mobile phone.",
                conversation_id=None,
                context=Context(),
                language="en",
                agent_id=entry.entry_id,
            )
        except _ProviderRequestCaptured:
            pass
    finally:
        entry.runtime_data = original_client

    assert len(calls) == 1
    return calls[0]


def _assert_enum_semantics(tool: dict[str, Any]) -> None:
    from homeassistant.exceptions import HomeAssistantError

    function_execution = importlib.import_module(
        f"custom_components.{DOMAIN}.function_execution"
    )
    validate = function_execution.validate_function_arguments
    assert validate(tool["spec"], {"phone": "mobile"}) == {"phone": "mobile"}
    with pytest.raises(HomeAssistantError):
        validate(tool["spec"], {"phone": "fax"})


async def _released_phase(hass: Any, config_dir: Path) -> None:
    """Persist the regression-class tool through the actual 6.8.3 management API."""
    from homeassistant.config_entries import ConfigEntryState

    entry = await _create_released_entry(hass)
    subentry = _conversation_subentry(entry)

    save_result, config_result = await _management_ws_calls(
        hass,
        [
            {
                "section": "tools",
                "action": "save",
                "entry_id": entry.entry_id,
                "subentry_id": subentry.subentry_id,
                "tool": _custom_tool(),
            },
            {
                "section": "configuration",
                "action": "get",
                "entry_id": entry.entry_id,
                "subentry_id": subentry.subentry_id,
            },
        ],
    )
    assert save_result.get("status") in {None, "saved"}
    release_tool = _find_custom_tool(config_result["config"]["functions"])
    _assert_phone_schema(release_tool)
    _assert_enum_semantics(release_tool)

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    subentry = _conversation_subentry(entry)
    config_after_reload = (
        await _management_ws_calls(
            hass,
            [
                {
                    "section": "configuration",
                    "action": "get",
                    "entry_id": entry.entry_id,
                    "subentry_id": subentry.subentry_id,
                }
            ],
        )
    )[0]
    release_tool = _find_custom_tool(config_after_reload["config"]["functions"])
    _assert_phone_schema(release_tool)

    provider_call = await _capture_real_provider_request(hass, entry)
    provider_tool = _provider_function_tool(provider_call)
    _assert_phone_schema({"spec": {"parameters": provider_tool["parameters"]}})

    (config_dir / _STATE_FILE).write_text(
        json.dumps(
            {
                "entry_id": entry.entry_id,
                "subentry_id": subentry.subentry_id,
                "title": subentry.title,
            }
        ),
        encoding="utf-8",
    )


async def _candidate_migration_phase(hass: Any, config_dir: Path) -> None:
    """Exercise management, schema semantics, and real provider assembly after upgrade."""
    from homeassistant.config_entries import ConfigEntryState

    state = json.loads((config_dir / _STATE_FILE).read_text(encoding="utf-8"))
    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1
    entry = entries[0]
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert entry.entry_id == state["entry_id"]
    subentry = _conversation_subentry(entry)
    assert subentry.subentry_id == state["subentry_id"]

    agents_result, config_result = await _management_ws_calls(
        hass,
        [
            {"action": "agents"},
            {
                "section": "configuration",
                "action": "get",
                "entry_id": entry.entry_id,
                "subentry_id": subentry.subentry_id,
            },
        ],
    )
    assert any(
        agent.get("subentry_id") == state["subentry_id"]
        for agent in agents_result["agents"]
    )
    candidate_tool = _find_custom_tool(config_result["config"]["functions"])
    _assert_phone_schema(candidate_tool)
    _assert_enum_semantics(candidate_tool)

    provider_call = await _capture_real_provider_request(hass, entry)
    provider_tool = _provider_function_tool(provider_call)
    _assert_phone_schema({"spec": {"parameters": provider_tool["parameters"]}})

    edited_tool = _custom_tool("Candidate-saved custom phone tool")
    await _management_ws_calls(
        hass,
        [
            {
                "section": "tools",
                "action": "save",
                "entry_id": entry.entry_id,
                "subentry_id": subentry.subentry_id,
                "original_name": _CUSTOM_TOOL_NAME,
                "tool": edited_tool,
            }
        ],
    )
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    reloaded = _conversation_subentry(entry)
    assert reloaded.subentry_id == state["subentry_id"]
    reloaded_config = (
        await _management_ws_calls(
            hass,
            [
                {
                    "section": "configuration",
                    "action": "get",
                    "entry_id": entry.entry_id,
                    "subentry_id": reloaded.subentry_id,
                }
            ],
        )
    )[0]
    saved_tool = _find_custom_tool(reloaded_config["config"]["functions"])
    assert saved_tool["spec"]["description"] == "Candidate-saved custom phone tool"
    _assert_phone_schema(saved_tool)

    state["candidate_title"] = reloaded.title
    (config_dir / _STATE_FILE).write_text(json.dumps(state), encoding="utf-8")


async def _candidate_restart_phase(hass: Any, config_dir: Path) -> None:
    """Cold-start the candidate and prove the custom tool remains fully usable."""
    from homeassistant.config_entries import ConfigEntryState

    state = json.loads((config_dir / _STATE_FILE).read_text(encoding="utf-8"))
    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1
    entry = entries[0]
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert entry.entry_id == state["entry_id"]
    subentry = _conversation_subentry(entry)
    assert subentry.subentry_id == state["subentry_id"]

    agents_result, config_result = await _management_ws_calls(
        hass,
        [
            {"action": "agents"},
            {
                "section": "configuration",
                "action": "get",
                "entry_id": entry.entry_id,
                "subentry_id": subentry.subentry_id,
            },
        ],
    )
    assert any(
        agent.get("subentry_id") == state["subentry_id"]
        for agent in agents_result["agents"]
    )
    tool = _find_custom_tool(config_result["config"]["functions"])
    assert tool["spec"]["description"] == "Candidate-saved custom phone tool"
    _assert_phone_schema(tool)
    _assert_enum_semantics(tool)

    provider_call = await _capture_real_provider_request(hass, entry)
    provider_tool = _provider_function_tool(provider_call)
    _assert_phone_schema({"spec": {"parameters": provider_tool["parameters"]}})


async def _child_main() -> None:
    from homeassistant import bootstrap, runner

    config_dir = Path(os.environ[_CONFIG_DIR_ENV]).resolve()
    phase = os.environ[_CHILD_PHASE_ENV]
    sys.path.insert(0, str(config_dir))

    hass = await bootstrap.async_setup_hass(
        runner.RuntimeConfig(config_dir=str(config_dir), skip_pip=False)
    )
    assert hass is not None
    await hass.async_start()
    try:
        if phase == "released":
            await _released_phase(hass, config_dir)
        elif phase == "candidate-migrate":
            await _candidate_migration_phase(hass, config_dir)
        elif phase == "candidate-restart":
            await _candidate_restart_phase(hass, config_dir)
        else:
            raise AssertionError(f"Unknown custom-tool upgrade phase: {phase}")
        await hass.async_block_till_done()
    finally:
        await hass.async_stop()


def test_published_release_custom_function_tool_survives_candidate_upgrade(
    tmp_path: Path,
) -> None:
    """Exercise a 6.8.3-compatible custom tool through a real release upgrade."""
    from_component = Path(os.environ[_FROM_COMPONENT_ENV]).resolve()
    to_component = Path(os.environ[_TO_COMPONENT_ENV]).resolve()
    from_manifest = _manifest(from_component)
    to_manifest = _manifest(to_component)

    assert from_manifest["domain"] == DOMAIN
    assert to_manifest["domain"] == DOMAIN
    expected_from = os.environ.get(_FROM_VERSION_ENV)
    expected_to = os.environ.get(_TO_VERSION_ENV)
    if expected_from:
        assert from_manifest["version"] == expected_from
    if expected_to:
        assert to_manifest["version"] == expected_to

    config_dir = tmp_path / "ha-custom-tool-config"
    config_dir.mkdir()
    (config_dir / "configuration.yaml").write_text(
        "homeassistant:\n  name: Custom Function Tool Upgrade Acceptance\n",
        encoding="utf-8",
    )

    _install_component(from_component, config_dir)
    released = _run_child(config_dir, "released")
    _assert_child_ok(released, "released")
    assert (config_dir / ".storage" / "core.config_entries").exists()
    assert (config_dir / _STATE_FILE).exists()

    _install_component(to_component, config_dir)
    assert _component_digest(from_component) != _component_digest(to_component) or (
        from_manifest["version"] != to_manifest["version"]
    )

    migrated = _run_child(config_dir, "candidate-migrate")
    _assert_child_ok(migrated, "candidate-migrate")

    restarted = _run_child(config_dir, "candidate-restart")
    _assert_child_ok(restarted, "candidate-restart")


if __name__ == "__main__" and os.environ.get(_CHILD_PHASE_ENV):
    asyncio.run(_child_main())
