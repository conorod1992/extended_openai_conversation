"""Real process-level acceptance for upgrading a published release to a candidate."""

from __future__ import annotations

import asyncio
import hashlib
import importlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any
from unittest.mock import AsyncMock, patch

import yaml

DOMAIN = "extended_openai_conversation_responses"
_FROM_COMPONENT_ENV = "UPGRADE_FROM_COMPONENT_DIR"
_TO_COMPONENT_ENV = "UPGRADE_TO_COMPONENT_DIR"
_FROM_VERSION_ENV = "UPGRADE_FROM_VERSION"
_TO_VERSION_ENV = "UPGRADE_TO_VERSION"
_CHILD_PHASE_ENV = "UPGRADE_ACCEPTANCE_CHILD_PHASE"
_CONFIG_DIR_ENV = "UPGRADE_ACCEPTANCE_CONFIG_DIR"
_STATE_FILE = "upgrade-acceptance-state.json"
_BACKUP_FILE = "upgrade-acceptance-current-backup.json"

if not os.environ.get(_CHILD_PHASE_ENV):
    # Standalone HA child processes do not depend on the parent's test framework.
    import pytest

    pytestmark = pytest.mark.skipif(
        not os.environ.get(_FROM_COMPONENT_ENV)
        or not os.environ.get(_TO_COMPONENT_ENV),
        reason="requires released and candidate component payloads",
    )


def _manifest(component_dir: Path) -> dict[str, Any]:
    return json.loads((component_dir / "manifest.json").read_text(encoding="utf-8"))


def _component_digest(component_dir: Path) -> str:
    """Return a deterministic digest proving the old and candidate payloads differ."""
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
    """Ensure HA initializes recorder before integration dependency setup."""
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
    # Published pins may conflict with the candidate HA's core SDK constraint.
    # Create historical state in its declared compatible runtime, then carry the
    # unchanged configuration/storage into the current candidate runtime.
    interpreter = (
        os.environ.get("UPGRADE_RELEASED_PYTHON", sys.executable)
        if phase == "released"
        else sys.executable
    )
    return subprocess.run(
        [interpreter, str(Path(__file__).resolve())],
        cwd=config_dir,
        env=env,
        text=True,
        capture_output=True,
        timeout=150,
        check=False,
    )


def _assert_child_ok(result: subprocess.CompletedProcess[str], phase: str) -> None:
    assert result.returncode == 0, (
        f"upgrade acceptance phase {phase!r} failed\n"
        f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    )


def _tool_names(data: Any) -> list[str]:
    """Extract configured Function Tool names without depending on either release."""
    if isinstance(data, str):
        try:
            data = yaml.safe_load(data)
        except yaml.YAMLError:
            return []
    if not isinstance(data, list):
        return []
    names: list[str] = []
    for tool in data:
        if not isinstance(tool, dict):
            continue
        spec = tool.get("spec")
        if isinstance(spec, dict) and isinstance(spec.get("name"), str):
            names.append(spec["name"])
    return names


def _chat_sse_tool_call(call_id: str, name: str, arguments: dict[str, Any]) -> bytes:
    chunk = {
        "id": "chatcmpl-upgrade-tool",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "gpt-5.6",
        "choices": [
            {
                "index": 0,
                "delta": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": json.dumps(arguments, separators=(",", ":")),
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
    }
    return f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n".encode()


def _chat_sse_text(text: str) -> bytes:
    chunk = {
        "id": "chatcmpl-upgrade-text",
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


def _unwrap_sdk_client(agent: Any) -> Any:
    client = agent._client
    while hasattr(client, "_delegate"):
        client = client._delegate
    return client


async def _exercise_populated_provider_journey(
    hass: Any, entry_id: str, state: dict[str, Any], expected: str
) -> None:
    """Prove migrated release-owned data and tools through the real SDK wire."""
    import httpx
    from homeassistant.components import conversation
    from homeassistant.core import Context

    agent = conversation.async_get_agent(hass, entry_id)
    assert agent is not None
    requests: list[dict[str, Any]] = []
    replies = [
        _chat_sse_tool_call(
            "upgrade-memory",
            "memory_search",
            {"query": "release memory marker", "scope": "personal", "limit": 5},
        ),
        _chat_sse_tool_call(
            "upgrade-knowledge",
            "knowledge_search",
            {"query": "release knowledge marker", "limit": 5},
        ),
        _chat_sse_tool_call("upgrade-function", "upgrade_marker", {}),
        _chat_sse_text(expected),
    ]

    async def send(request: Any, *args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        body = json.loads(request.content)
        requests.append(body)
        index = len(requests) - 1
        assert index < len(replies), "Unexpected provider request during upgrade journey"
        if index == 0:
            names = {
                item["function"]["name"]
                for item in body.get("tools", [])
                if isinstance(item, dict) and isinstance(item.get("function"), dict)
            }
            assert {"memory_search", "knowledge_search", "upgrade_marker"} <= names
        elif index == 1:
            assert state["memory_marker"] in json.dumps(body)
        elif index == 2:
            assert state["knowledge_marker"] in json.dumps(body)
        elif index == 3:
            serialized = json.dumps(body)
            assert "UPGRADE_TOOL_RESULT" in serialized
            assert state["memory_marker"] in serialized
            assert state["knowledge_marker"] in serialized
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=replies[index],
            request=request,
        )

    sdk = _unwrap_sdk_client(agent)
    original_send = sdk._client.send
    sdk._client.send = send
    try:
        result = await conversation.async_converse(
            hass=hass,
            text="Use the release memory, release knowledge, and upgrade marker tool.",
            conversation_id=None,
            context=Context(user_id=state["owner_id"]),
            language="en",
            agent_id=entry_id,
        )
    finally:
        sdk._client.send = original_send

    assert result.response.error_code is None
    assert result.response.as_dict()["speech"]["plain"]["speech"] == expected
    assert len(requests) == 4


async def _assert_populated_release_state(
    hass: Any, entry_id: str, state: dict[str, Any]
) -> None:
    """Read the exact release-created durable records through candidate managers."""
    from homeassistant.components import conversation

    agent = conversation.async_get_agent(hass, entry_id)
    assert agent is not None
    memories = await agent._memory.async_list(state["owner_id"], limit=100)
    assert any(item.content == state["memory_marker"] for item in memories)
    source = await agent._knowledge.async_get(state["knowledge_source_id"])
    assert source.content == state["knowledge_marker"]
    rules = agent._request_rules.snapshot()["rules"]
    assert any(
        rule["name"] == "Release upgrade local rule"
        and rule["action"]["success_response"] == state["request_rule_marker"]
        for rule in rules
    )


async def _exercise_release_rule(
    hass: Any, entry_id: str, state: dict[str, Any]
) -> None:
    from homeassistant.components import conversation
    from homeassistant.core import Context

    result = await conversation.async_converse(
        hass=hass,
        text="release upgrade local rule",
        conversation_id=None,
        context=Context(user_id=state["owner_id"]),
        language="en",
        agent_id=entry_id,
    )
    assert result.response.error_code is None
    assert (
        result.response.as_dict()["speech"]["plain"]["speech"]
        == state["request_rule_marker"]
    )


async def _exercise_public_conversation(
    hass: Any, entry_id: str, expected: str
) -> None:
    """Prove the loaded agent remains usable through HA's public conversation API."""
    from homeassistant.components import conversation
    from homeassistant.core import Context

    agent = conversation.async_get_agent(hass, entry_id)
    assert agent is not None

    async def model(log: Any, **kwargs: Any) -> None:
        del kwargs
        log.async_add_assistant_content_without_tools(
            conversation.AssistantContent(agent_id=agent.entity_id, content=expected)
        )

    original = agent._async_handle_chat_log
    agent._async_handle_chat_log = model
    try:
        result = await conversation.async_converse(
            hass=hass,
            text="Confirm the release upgrade acceptance agent is healthy",
            conversation_id=None,
            context=Context(),
            language="en",
            agent_id=entry_id,
        )
    finally:
        agent._async_handle_chat_log = original

    assert result.response.error_code is None
    assert result.response.as_dict()["speech"]["plain"]["speech"] == expected


def _conversation_subentry(entry: Any) -> Any:
    return next(
        subentry
        for subentry in entry.subentries.values()
        if subentry.subentry_type == "conversation"
    )


async def _create_released_entry(hass: Any) -> Any:
    """Create an entry using the installed released integration's own config flow."""
    from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
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
            CONF_NAME: "Published Release Upgrade Acceptance",
            CONF_API_KEY: "sk-release-upgrade-acceptance",
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


async def _released_phase(hass: Any, config_dir: Path) -> None:
    """Have the actual published release create and persist representative state."""
    from homeassistant.config_entries import ConfigEntryState

    const = importlib.import_module(f"custom_components.{DOMAIN}.const")
    entry = await _create_released_entry(hass)
    subentry = _conversation_subentry(entry)

    # Keep the release's own complete default representation, including its exact
    # historical stock Function Tool schemas, and customize only stable user fields.
    data = dict(subentry.data)
    custom_values: dict[str, Any] = {}
    for name, value in (
        ("CONF_CHAT_MODEL", "gpt-5.6"),
        ("CONF_REASONING_EFFORT", "medium"),
        ("CONF_TEMPERATURE", 0.42),
    ):
        key = getattr(const, name, None)
        if isinstance(key, str) and key in data:
            data[key] = value
            custom_values[key] = value

    # Enable durable features that are present in the published release and add
    # one harmless user-defined Function Tool using only stable configuration shapes.
    for constant, value in (
        ("CONF_API_MODE", "chat_completions"),
        ("CONF_MEMORY_MODE", "manual"),
        ("CONF_MEMORY_AUTO_RETRIEVE_LIMIT", 3),
        ("CONF_KNOWLEDGE_ENABLED", True),
    ):
        key = getattr(const, constant, None)
        if isinstance(key, str):
            data[key] = value
            custom_values[key] = value

    function_tools_key = getattr(const, "CONF_FUNCTION_TOOLS", "function_tools")
    raw_tools = data.get(function_tools_key, [])
    tools = yaml.safe_load(raw_tools) if isinstance(raw_tools, str) else list(raw_tools or [])
    tools.append(
        {
            "spec": {
                "name": "upgrade_marker",
                "description": "Return a durable upgrade acceptance marker.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            },
            "function": {
                "type": "template",
                "value_template": "UPGRADE_TOOL_RESULT",
            },
            "enabled": True,
        }
    )
    # Persist the release's canonical storage representation. 6.8.3 stores
    # Function Tools as YAML text even though frontend snapshots expose a list.
    # Writing a raw list directly into the subentry bypasses that normalization
    # and creates an artificial migration shape no real user save would produce.
    data[function_tools_key] = yaml.safe_dump(
        tools, sort_keys=False, allow_unicode=True
    )

    hass.config_entries.async_update_subentry(
        entry,
        subentry,
        data=data,
        title="Upgrade Acceptance Agent",
    )
    await hass.async_block_till_done()

    # A real reload proves the released runtime can consume what it just persisted.
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    subentry = _conversation_subentry(entry)
    assert subentry.title == "Upgrade Acceptance Agent"
    agent = importlib.import_module("homeassistant.components.conversation").async_get_agent(
        hass, entry.entry_id
    )
    assert agent is not None
    owner = await hass.auth.async_create_user("Release upgrade owner")
    memory_marker = "RELEASE_MEMORY_MARKER release memory marker"
    knowledge_marker = "RELEASE_KNOWLEDGE_MARKER release knowledge marker"
    assert agent._memory is not None
    await agent._memory.async_add(
        owner.id, memory_marker, "upgrade", "explicit", key="upgrade.release.memory"
    )
    assert agent._knowledge is not None
    # Use the oldest supported published-release call shape. Newer candidates
    # default enabled=True, while 6.8.3 predates the explicit enabled argument.
    source = await agent._knowledge.async_create(
        "Release upgrade reference",
        "Created by the published release",
        knowledge_marker,
    )
    await agent._request_rules.async_create(
        {
            "name": "Release upgrade local rule",
            "enabled": True,
            "phrases": ["release upgrade local rule"],
            "match_type": "equals",
            "action_type": "local_action",
            "action": {
                # 6.8.3 already required at least one native HA Script action.
                # A tiny delay is side-effect free while exercising persisted
                # local-action semantics across the release boundary.
                "actions": [{"delay": {"milliseconds": 1}}],
                "success_response": "RELEASE_RULE_MARKER",
                "failure_response": "RELEASE_RULE_FAILED",
            },
        }
    )
    await _exercise_public_conversation(
        hass, entry.entry_id, "Published release state is healthy."
    )

    function_tools_key = getattr(const, "CONF_FUNCTION_TOOLS", "function_tools")
    state = {
        "entry_id": entry.entry_id,
        "subentry_id": subentry.subentry_id,
        "entry_version": entry.version,
        "title": subentry.title,
        "custom_values": custom_values,
        "function_tool_names": _tool_names(subentry.data.get(function_tools_key)),
        "owner_id": owner.id,
        "memory_marker": memory_marker,
        "knowledge_marker": knowledge_marker,
        "knowledge_source_id": source.source_id,
        "request_rule_marker": "RELEASE_RULE_MARKER",
        "released_runtime": {
            "homeassistant": version("homeassistant"),
            "openai": version("openai"),
        },
    }
    expected_ha = os.environ.get("UPGRADE_RELEASED_HA_VERSION")
    if expected_ha:
        assert state["released_runtime"]["homeassistant"] == expected_ha
    print(f"Published release runtime: {state['released_runtime']}", flush=True)
    (config_dir / _STATE_FILE).write_text(json.dumps(state), encoding="utf-8")


async def _candidate_migration_phase(hass: Any, config_dir: Path) -> None:
    """Load release-produced state with the candidate, then save current state."""
    from homeassistant.config_entries import ConfigEntryState

    const = importlib.import_module(f"custom_components.{DOMAIN}.const")
    state = json.loads((config_dir / _STATE_FILE).read_text(encoding="utf-8"))

    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1
    entry = entries[0]
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert entry.entry_id == state["entry_id"]
    assert entry.version == const.CONFIG_ENTRY_VERSION

    subentry = _conversation_subentry(entry)
    assert subentry.subentry_id == state["subentry_id"]
    assert subentry.title == state["title"]
    for key, value in state["custom_values"].items():
        assert subentry.data.get(key) == value

    function_tools_key = getattr(const, "CONF_FUNCTION_TOOLS", "function_tools")
    candidate_tool_names = set(_tool_names(subentry.data.get(function_tools_key)))
    assert set(state["function_tool_names"]).issubset(candidate_tool_names)

    await _assert_populated_release_state(hass, entry.entry_id, state)
    await _exercise_release_rule(hass, entry.entry_id, state)
    await _exercise_populated_provider_journey(
        hass, entry.entry_id, state, "Candidate migrated release state successfully."
    )

    # Save through Home Assistant's supported config-subentry mutation boundary,
    # then reload the entry so the candidate must consume its own post-migration state.
    edited = dict(subentry.data)
    reasoning_key = getattr(const, "CONF_REASONING_EFFORT", None)
    if isinstance(reasoning_key, str) and reasoning_key in edited:
        edited[reasoning_key] = "high"
        state["post_upgrade_reasoning"] = {"key": reasoning_key, "value": "high"}

    hass.config_entries.async_update_subentry(
        entry,
        subentry,
        data=edited,
        title="Upgrade Acceptance Agent - Candidate Saved",
    )
    await hass.async_block_till_done()
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    reloaded = _conversation_subentry(entry)
    assert reloaded.subentry_id == state["subentry_id"]
    assert reloaded.title == "Upgrade Acceptance Agent - Candidate Saved"
    await _assert_populated_release_state(hass, entry.entry_id, state)
    await _exercise_release_rule(hass, entry.entry_id, state)
    await _exercise_populated_provider_journey(
        hass, entry.entry_id, state, "Candidate save and reload are healthy."
    )

    state["candidate_title"] = reloaded.title
    state["candidate_entry_version"] = entry.version
    from custom_components.extended_openai_conversation_responses import backup

    snapshot = await backup.async_collect_backup_snapshot(hass, entry, reloaded)
    (config_dir / _BACKUP_FILE).write_text(
        json.dumps(snapshot, ensure_ascii=False), encoding="utf-8"
    )
    (config_dir / _STATE_FILE).write_text(json.dumps(state), encoding="utf-8")


async def _candidate_restart_phase(hass: Any, config_dir: Path) -> None:
    """Cold-start the candidate again and prove its migrated save remains healthy."""
    from homeassistant.config_entries import ConfigEntryState

    const = importlib.import_module(f"custom_components.{DOMAIN}.const")
    state = json.loads((config_dir / _STATE_FILE).read_text(encoding="utf-8"))

    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1
    entry = entries[0]
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert entry.entry_id == state["entry_id"]
    assert (
        entry.version == const.CONFIG_ENTRY_VERSION == state["candidate_entry_version"]
    )

    subentry = _conversation_subentry(entry)
    assert subentry.subentry_id == state["subentry_id"]
    assert subentry.title == state["candidate_title"]
    reasoning = state.get("post_upgrade_reasoning")
    if reasoning:
        assert subentry.data.get(reasoning["key"]) == reasoning["value"]

    await _assert_populated_release_state(hass, entry.entry_id, state)
    await _exercise_release_rule(hass, entry.entry_id, state)
    await _exercise_populated_provider_journey(
        hass, entry.entry_id, state, "Migrated candidate state survived a cold restart."
    )

    # A backup made *after* migration must recover from subsequent user changes.
    from custom_components.extended_openai_conversation_responses import backup

    saved = json.loads((config_dir / _BACKUP_FILE).read_text(encoding="utf-8"))
    hass.config_entries.async_update_subentry(
        entry, subentry, data={}, title="Deliberately mutated after backup"
    )
    await hass.async_block_till_done()
    assert (await backup.async_restore_backup(hass, entry, subentry, saved))[
        "status"
    ] == "restored"
    await hass.async_block_till_done()
    restored = _conversation_subentry(entry)
    assert restored.title == state["candidate_title"]
    assert (
        backup.export_configuration_snapshot(restored.data) == saved["agent"]["config"]
    )
    await _assert_populated_release_state(hass, entry.entry_id, state)
    await _exercise_release_rule(hass, entry.entry_id, state)
    await _exercise_populated_provider_journey(
        hass, entry.entry_id, state, "Migrated candidate backup restored successfully."
    )


async def _child_main() -> None:
    """Boot one independent HA process for one upgrade phase."""
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
            raise AssertionError(f"Unknown upgrade acceptance phase: {phase}")
        await hass.async_block_till_done()
    finally:
        await hass.async_stop()


def test_published_release_upgrades_to_candidate_and_survives_restart(
    socket_enabled,
    tmp_path: Path,
    unused_tcp_port: int,
) -> None:
    """Upgrade state made by a real release to the candidate across HA processes."""
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

    config_dir = tmp_path / "ha-config"
    config_dir.mkdir()
    (config_dir / "configuration.yaml").write_text(
        f"homeassistant:\n  name: Release Upgrade Acceptance\nhttp:\n  server_host: 127.0.0.1\n  server_port: {unused_tcp_port}\n",
        encoding="utf-8",
    )

    _install_component(from_component, config_dir)
    released = _run_child(config_dir, "released")
    _assert_child_ok(released, "released")
    assert (config_dir / ".storage" / "core.config_entries").exists()
    assert (config_dir / _STATE_FILE).exists()

    # Replace only the integration payload. The exact same HA config/storage is
    # carried forward, which is the user-visible upgrade boundary under test.
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
