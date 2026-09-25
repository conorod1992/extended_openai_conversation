"""Real process-level acceptance for upgrading a published release to a candidate."""

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
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
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

pytestmark = pytest.mark.skipif(
    not os.environ.get(_FROM_COMPONENT_ENV) or not os.environ.get(_TO_COMPONENT_ENV),
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


async def _exercise_public_conversation(hass: Any, entry_id: str, expected: str) -> None:
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
    }
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

    await _exercise_public_conversation(
        hass, entry.entry_id, "Candidate migrated release state successfully."
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
    await _exercise_public_conversation(
        hass, entry.entry_id, "Candidate save and reload are healthy."
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
    assert entry.version == const.CONFIG_ENTRY_VERSION == state["candidate_entry_version"]

    subentry = _conversation_subentry(entry)
    assert subentry.subentry_id == state["subentry_id"]
    assert subentry.title == state["candidate_title"]
    reasoning = state.get("post_upgrade_reasoning")
    if reasoning:
        assert subentry.data.get(reasoning["key"]) == reasoning["value"]

    await _exercise_public_conversation(
        hass, entry.entry_id, "Migrated candidate state survived a cold restart."
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
    assert restored.data == saved["agent"]["config"]
    await _exercise_public_conversation(
        hass, entry.entry_id, "Migrated candidate backup restored successfully."
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
    tmp_path: Path,
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
        "homeassistant:\n  name: Release Upgrade Acceptance\n",
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
