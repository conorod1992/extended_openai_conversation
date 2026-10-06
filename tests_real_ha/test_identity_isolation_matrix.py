"""Real-HA isolation for reused names and the original integration side by side."""

from __future__ import annotations

import asyncio
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import MappingProxyType
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, MockUser

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
    CONF_API_MODE,
    CONF_MEMORY_MODE,
    CONF_SKIP_AUTHENTICATION,
    CONFIG_ENTRY_VERSION,
    DEFAULT_AI_TASK_OPTIONS,
    DOMAIN,
    MEMORY_MODE_MANUAL,
    SERVICE_PROCESS,
)
from custom_components.extended_openai_conversation_responses.local_intents import (
    CONF_LOCAL_INTENTS_ENABLED,
)
from custom_components.extended_openai_conversation_responses.management_ui import (
    WS_COMMAND,
)
from homeassistant.components import ai_task, conversation
from homeassistant.config_entries import ConfigEntryState, ConfigSubentry
from homeassistant.const import CONF_API_KEY, CONF_NAME
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import entity_registry as er
from tests_real_ha.process_harness import ensure_staged_custom_components_package
from tests_real_ha.test_acceptance_lifecycle import _setup_entry, _subentry
from tests_real_ha.test_ai_task_runtime import FakeClient
from tests_real_ha.test_management_backend_acceptance import _admin_client

_ORIGINAL_DOMAIN = "extended_openai_conversation"
_ORIGINAL_RELEASE = "2.0.2"
_ORIGINAL_SHA = "ceb3dc224c1d59e526a565818b618daafeae3855"
_SIDE_BY_SIDE_CHILD = "EOAI_SIDE_BY_SIDE_CHILD"
_SIDE_BY_SIDE_CONFIG = "EOAI_SIDE_BY_SIDE_CONFIG"
_SHARED_CONVERSATION_TITLE = "Shared Assistant"
_SHARED_TASK_TITLE = "Shared AI Task"
_OWNER = "identity-isolation-owner"


def _identity_entry(title: str) -> MockConfigEntry:
    """Create one parent with intentionally reused child display names."""
    ai_options = dict(DEFAULT_AI_TASK_OPTIONS)
    ai_options[CONF_API_MODE] = API_MODE_CHAT_COMPLETIONS
    return MockConfigEntry(
        domain=DOMAIN,
        title=title,
        data={
            CONF_API_KEY: "sk-identity-isolation",
            CONF_SKIP_AUTHENTICATION: True,
        },
        version=CONFIG_ENTRY_VERSION,
        subentries_data=[
            _subentry(
                "conversation",
                _SHARED_CONVERSATION_TITLE,
                {
                    CONF_LOCAL_INTENTS_ENABLED: True,
                    CONF_MEMORY_MODE: MEMORY_MODE_MANUAL,
                },
            ),
            _subentry("ai_task_data", _SHARED_TASK_TITLE, ai_options),
        ],
    )


def _entry_subentry(entry: MockConfigEntry, kind: str):
    return next(item for item in entry.subentries.values() if item.subentry_type == kind)


def _row(hass: HomeAssistant, entry: MockConfigEntry, domain: str):
    return next(
        item
        for item in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
        if item.domain == domain
    )


def _speech(result: Any) -> str:
    return result.response.as_dict()["speech"]["plain"]["speech"]


async def _catalog(client: Any) -> list[dict[str, Any]]:
    await client.send_json_auto_id(
        {"type": WS_COMMAND, "section": "overview", "action": "agents"}
    )
    response = await client.receive_json()
    assert response["success"], response
    return response["result"]["agents"]


@pytest.mark.asyncio
async def test_duplicate_conversation_titles_across_parents_keep_identity(
    hass: HomeAssistant,
    hass_ws_client: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Management, Assist and private Memory stay keyed by entry/subentry identity."""
    MockUser(id=_OWNER, name="Identity isolation owner", is_owner=True).add_to_hass(hass)
    first = _identity_entry("Provider A")
    second = _identity_entry("Provider B")
    await _setup_entry(hass, first)
    await _setup_entry(hass, second)

    first_subentry = _entry_subentry(first, "conversation")
    second_subentry = _entry_subentry(second, "conversation")
    assert first_subentry.title == second_subentry.title == _SHARED_CONVERSATION_TITLE
    assert first.entry_id != second.entry_id
    assert first_subentry.subentry_id != second_subentry.subentry_id

    client = await _admin_client(
        hass,
        hass_ws_client,
        user_id="duplicate-title-admin",
        name="Duplicate title admin",
    )
    agents = await _catalog(client)
    matching = [
        item
        for item in agents
        if item["title"] == _SHARED_CONVERSATION_TITLE
        and item["entry_id"] in {first.entry_id, second.entry_id}
    ]
    assert {
        (item["entry_id"], item["subentry_id"], item["entry_title"])
        for item in matching
    } == {
        (first.entry_id, first_subentry.subentry_id, first.title),
        (second.entry_id, second_subentry.subentry_id, second.title),
    }

    first_row = _row(hass, first, conversation.DOMAIN)
    second_row = _row(hass, second, conversation.DOMAIN)
    first_agent = conversation.async_get_agent(hass, first_row.entity_id)
    second_agent = conversation.async_get_agent(hass, second_row.entity_id)
    assert first_agent is not None and second_agent is not None
    assert first_agent is not second_agent
    assert first_agent._memory is not None and second_agent._memory is not None

    async def answer(agent: Any, text: str, log: Any, **kwargs: Any) -> None:
        del kwargs
        log.async_add_assistant_content_without_tools(
            conversation.AssistantContent(agent_id=agent.entity_id, content=text)
        )

    monkeypatch.setattr(
        first_agent,
        "_async_handle_chat_log",
        lambda log, **kwargs: answer(first_agent, "Provider A reply", log, **kwargs),
    )
    monkeypatch.setattr(
        second_agent,
        "_async_handle_chat_log",
        lambda log, **kwargs: answer(second_agent, "Provider B reply", log, **kwargs),
    )

    first_result = await conversation.async_converse(
        hass=hass,
        text="Identify this assistant",
        conversation_id=None,
        context=Context(user_id=_OWNER),
        language="en",
        agent_id=first_row.entity_id,
    )
    second_result = await conversation.async_converse(
        hass=hass,
        text="Identify this assistant",
        conversation_id=None,
        context=Context(user_id=_OWNER),
        language="en",
        agent_id=second_row.entity_id,
    )
    assert _speech(first_result) == "Provider A reply"
    assert _speech(second_result) == "Provider B reply"

    await first_agent._memory.async_add(
        _OWNER, "PRIVATE_PROVIDER_A_MARKER", "identity", "explicit"
    )
    await second_agent._memory.async_add(
        _OWNER, "PRIVATE_PROVIDER_B_MARKER", "identity", "explicit"
    )
    assert [
        item.content
        for item in await first_agent._memory.async_search(
            _OWNER, "PRIVATE_PROVIDER_A_MARKER"
        )
    ] == ["PRIVATE_PROVIDER_A_MARKER"]
    assert [
        item.content
        for item in await second_agent._memory.async_search(
            _OWNER, "PRIVATE_PROVIDER_B_MARKER"
        )
    ] == ["PRIVATE_PROVIDER_B_MARKER"]

    second_agent_before = second_agent
    assert await hass.config_entries.async_reload(first.entry_id)
    await hass.async_block_till_done()
    assert conversation.async_get_agent(hass, second_row.entity_id) is second_agent_before
    assert [
        item.content
        for item in await second_agent_before._memory.async_search(
            _OWNER, "PRIVATE_PROVIDER_B_MARKER"
        )
    ] == ["PRIVATE_PROVIDER_B_MARKER"]


@pytest.mark.asyncio
async def test_duplicate_ai_task_titles_across_parents_route_by_entity_identity(
    hass: HomeAssistant,
) -> None:
    """Identical AI Task friendly names never select the sibling parent runtime."""
    first = _identity_entry("Task Provider A")
    second = _identity_entry("Task Provider B")
    await _setup_entry(hass, first)
    await _setup_entry(hass, second)

    first_task = _entry_subentry(first, "ai_task_data")
    second_task = _entry_subentry(second, "ai_task_data")
    assert first_task.title == second_task.title == _SHARED_TASK_TITLE
    assert first_task.subentry_id != second_task.subentry_id

    first_row = _row(hass, first, ai_task.DOMAIN)
    second_row = _row(hass, second, ai_task.DOMAIN)
    assert first_row.entity_id != second_row.entity_id
    assert first_row.config_subentry_id == first_task.subentry_id
    assert second_row.config_subentry_id == second_task.subentry_id

    first_client = FakeClient(["TASK_PROVIDER_A_RESULT"])
    second_client = FakeClient(["TASK_PROVIDER_B_RESULT"])
    first.runtime_data = first_client
    second.runtime_data = second_client

    first_result = await ai_task.async_generate_data(
        hass,
        task_name="Same visible task",
        entity_id=first_row.entity_id,
        instructions="Return the first provider marker.",
    )
    second_result = await ai_task.async_generate_data(
        hass,
        task_name="Same visible task",
        entity_id=second_row.entity_id,
        instructions="Return the second provider marker.",
    )
    assert first_result.data == "TASK_PROVIDER_A_RESULT"
    assert second_result.data == "TASK_PROVIDER_B_RESULT"
    assert len(first_client.completions.calls) == 1
    assert len(second_client.completions.calls) == 1

    assert hass.config_entries.async_remove_subentry(first, first_task.subentry_id)
    await hass.async_block_till_done()
    assert not any(
        row.config_subentry_id == first_task.subentry_id
        for row in er.async_entries_for_config_entry(er.async_get(hass), first.entry_id)
    )
    assert _row(hass, second, ai_task.DOMAIN).entity_id == second_row.entity_id

    recreated = ConfigSubentry(
        data=MappingProxyType(dict(first_task.data)),
        subentry_type="ai_task_data",
        title=_SHARED_TASK_TITLE,
        unique_id=None,
    )
    assert hass.config_entries.async_add_subentry(first, recreated)
    await hass.async_block_till_done()
    recreated_row = _row(hass, first, ai_task.DOMAIN)
    assert recreated.subentry_id != first_task.subentry_id
    assert recreated_row.config_subentry_id == recreated.subentry_id

    replacement_client = FakeClient(["TASK_PROVIDER_A_RECREATED"])
    first.runtime_data = replacement_client
    recreated_result = await ai_task.async_generate_data(
        hass,
        task_name="Recreated same visible task",
        entity_id=recreated_row.entity_id,
        instructions="Use the recreated first provider task.",
    )
    assert recreated_result.data == "TASK_PROVIDER_A_RECREATED"
    assert len(replacement_client.completions.calls) == 1
    assert len(second_client.completions.calls) == 1


@pytest.mark.asyncio
async def test_deleted_parent_recreated_with_same_names_gets_fresh_generation(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Whole-parent replacement cannot inherit Memory, chat context or runtime identity."""
    MockUser(id=_OWNER, name="Identity isolation owner", is_owner=True).add_to_hass(hass)
    old = _identity_entry("Reusable Provider")
    await _setup_entry(hass, old)
    old_subentry = _entry_subentry(old, "conversation")
    old_row = _row(hass, old, conversation.DOMAIN)
    old_agent = conversation.async_get_agent(hass, old_row.entity_id)
    assert old_agent is not None and old_agent._memory is not None
    await old_agent._memory.async_add(
        _OWNER, "OLD_PARENT_PRIVATE_MARKER", "identity", "explicit"
    )

    async def old_answer(log: Any, **kwargs: Any) -> None:
        del kwargs
        log.async_add_assistant_content_without_tools(
            conversation.AssistantContent(
                agent_id=old_agent.entity_id,
                content="OLD_PARENT_RESPONSE_MARKER",
            )
        )

    monkeypatch.setattr(old_agent, "_async_handle_chat_log", old_answer)
    collision_id = "reused-parent-visible-conversation-id"
    old_result = await conversation.async_converse(
        hass=hass,
        text="Old parent turn",
        conversation_id=collision_id,
        context=Context(user_id=_OWNER),
        language="en",
        agent_id=old_row.entity_id,
    )
    assert _speech(old_result) == "OLD_PARENT_RESPONSE_MARKER"

    old_entry_id = old.entry_id
    old_subentry_id = old_subentry.subentry_id
    old_memory = old_agent._memory
    assert await hass.config_entries.async_remove(old.entry_id)
    await hass.async_block_till_done()
    assert hass.config_entries.async_get_entry(old_entry_id) is None

    replacement = _identity_entry("Reusable Provider")
    await _setup_entry(hass, replacement)
    replacement_subentry = _entry_subentry(replacement, "conversation")
    replacement_row = _row(hass, replacement, conversation.DOMAIN)
    replacement_agent = conversation.async_get_agent(hass, replacement_row.entity_id)
    assert replacement_agent is not None and replacement_agent._memory is not None
    assert replacement.entry_id != old_entry_id
    assert replacement_subentry.subentry_id != old_subentry_id
    assert replacement_agent is not old_agent
    assert replacement_agent._memory is not old_memory
    assert await replacement_agent._memory.async_search(
        _OWNER, "OLD_PARENT_PRIVATE_MARKER"
    ) == []

    seen: list[str] = []

    async def replacement_answer(log: Any, **kwargs: Any) -> None:
        del kwargs
        serialized = json.dumps(
            [getattr(item, "content", None) for item in log.content],
            ensure_ascii=False,
            default=str,
        )
        seen.append(serialized)
        assert "OLD_PARENT_RESPONSE_MARKER" not in serialized
        assert "OLD_PARENT_PRIVATE_MARKER" not in serialized
        log.async_add_assistant_content_without_tools(
            conversation.AssistantContent(
                agent_id=replacement_agent.entity_id,
                content="NEW_PARENT_RESPONSE_MARKER",
            )
        )

    monkeypatch.setattr(
        replacement_agent, "_async_handle_chat_log", replacement_answer
    )
    new_result = await conversation.async_converse(
        hass=hass,
        text="New parent turn",
        conversation_id=collision_id,
        context=Context(user_id=_OWNER),
        language="en",
        agent_id=replacement_row.entity_id,
    )
    assert _speech(new_result) == "NEW_PARENT_RESPONSE_MARKER"
    assert len(seen) == 1


def _assert_component_path(domain: str, config_dir: Path) -> None:
    module = importlib.import_module(f"custom_components.{domain}")
    path = Path(module.__file__).resolve()
    expected = (config_dir / "custom_components" / domain).resolve()
    assert expected in path.parents, f"{domain} loaded from unexpected path: {path}"


async def _create_real_flow_entry(hass: Any, domain: str, title: str) -> Any:
    """Create an entry using that installed integration's real config flow."""
    from homeassistant.config_entries import SOURCE_USER
    from homeassistant.data_entry_flow import FlowResultType

    flow_module = importlib.import_module(f"custom_components.{domain}.config_flow")
    const = importlib.import_module(f"custom_components.{domain}.const")
    with patch.object(
        flow_module,
        "get_authenticated_client",
        AsyncMock(return_value=object()),
    ):
        result = await hass.config_entries.flow.async_init(
            domain, context={"source": SOURCE_USER}
        )
        assert result["type"] is FlowResultType.FORM
        payload = {
            CONF_NAME: title,
            CONF_API_KEY: f"sk-{domain}-side-by-side",
            const.CONF_SKIP_AUTHENTICATION: True,
            const.CONF_API_PROVIDER: "openai",
        }
        if hasattr(const, "CONF_BASE_URL") and hasattr(const, "DEFAULT_CONF_BASE_URL"):
            payload[const.CONF_BASE_URL] = const.DEFAULT_CONF_BASE_URL
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], payload
        )
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    entry = result["result"]
    assert entry.state is ConfigEntryState.LOADED
    return entry


async def _side_by_side_child(config_dir: Path) -> None:
    """Boot both delivered integrations in one genuine Home Assistant process."""
    from homeassistant import bootstrap, runner
    from homeassistant.components.frontend import DATA_PANELS
    from homeassistant.exceptions import HomeAssistantError

    sys.path.insert(0, str(config_dir))
    hass = await bootstrap.async_setup_hass(
        runner.RuntimeConfig(config_dir=str(config_dir), skip_pip=False)
    )
    assert hass is not None
    await hass.async_start()
    try:
        original = await _create_real_flow_entry(
            hass, _ORIGINAL_DOMAIN, "Side by side Original"
        )
        fork = await _create_real_flow_entry(hass, DOMAIN, "Side by side Fork")

        _assert_component_path(_ORIGINAL_DOMAIN, config_dir)
        _assert_component_path(DOMAIN, config_dir)
        assert original.domain == _ORIGINAL_DOMAIN
        assert fork.domain == DOMAIN
        assert original.entry_id != fork.entry_id

        registry = er.async_get(hass)
        original_rows = er.async_entries_for_config_entry(registry, original.entry_id)
        fork_rows = er.async_entries_for_config_entry(registry, fork.entry_id)
        assert any(row.domain == conversation.DOMAIN for row in original_rows)
        assert any(row.domain == conversation.DOMAIN for row in fork_rows)
        assert any(row.domain == ai_task.DOMAIN for row in fork_rows)
        assert {row.entity_id for row in original_rows}.isdisjoint(
            {row.entity_id for row in fork_rows}
        )
        assert all(row.config_entry_id == original.entry_id for row in original_rows)
        assert all(row.config_entry_id == fork.entry_id for row in fork_rows)

        assert hass.services.has_service(_ORIGINAL_DOMAIN, "query_image")
        assert hass.services.has_service(_ORIGINAL_DOMAIN, "change_config")
        assert hass.services.has_service(DOMAIN, "query_image")
        assert hass.services.has_service(DOMAIN, SERVICE_PROCESS)

        fork_subentry = next(
            item for item in fork.subentries.values()
            if item.subentry_type == "conversation"
        )
        from custom_components.extended_openai_conversation_responses.memory import (
            async_get_memory,
        )
        fork_memory = await async_get_memory(
            hass, fork.entry_id, fork_subentry.subentry_id
        )
        await fork_memory.async_add(
            "side-by-side-owner",
            "FORK_SIDE_BY_SIDE_STORAGE_MARKER",
            "identity",
            "explicit",
        )
        storage_files = list((config_dir / ".storage").glob(f"{DOMAIN}.memory.*"))
        assert storage_files
        assert any(
            "FORK_SIDE_BY_SIDE_STORAGE_MARKER" in path.read_text(encoding="utf-8")
            for path in storage_files
        )
        assert not list(
            (config_dir / ".storage").glob(
                f"{_ORIGINAL_DOMAIN}.memory.{fork.entry_id}.*"
            )
        )

        fork_conversation_id = next(
            row.entity_id for row in fork_rows if row.domain == conversation.DOMAIN
        )
        original_conversation_id = next(
            row.entity_id for row in original_rows if row.domain == conversation.DOMAIN
        )
        fork_agent = conversation.async_get_agent(hass, fork_conversation_id)
        assert fork_agent is not None

        async def fork_service_answer(log: Any, **kwargs: Any) -> None:
            del kwargs
            log.async_add_assistant_content_without_tools(
                conversation.AssistantContent(
                    agent_id=fork_agent.entity_id,
                    content="FORK_SERVICE_ROUTE_MARKER",
                )
            )

        fork_agent._async_handle_chat_log = fork_service_answer
        response = await hass.services.async_call(
            DOMAIN,
            SERVICE_PROCESS,
            {"text": "route through the fork", "agent_id": fork_conversation_id},
            blocking=True,
            return_response=True,
        )
        assert response["response"] == "FORK_SERVICE_ROUTE_MARKER"
        with pytest.raises(HomeAssistantError):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_PROCESS,
                {"text": "must not cross domains", "agent_id": original_conversation_id},
                blocking=True,
                return_response=True,
            )

        original_workspace = config_dir / _ORIGINAL_DOMAIN
        fork_workspace = config_dir / DOMAIN
        original_workspace.mkdir(exist_ok=True)
        fork_workspace.mkdir(exist_ok=True)
        (original_workspace / "ownership-marker.txt").write_text(
            "ORIGINAL_WORKSPACE_MARKER", encoding="utf-8"
        )
        (fork_workspace / "ownership-marker.txt").write_text(
            "FORK_WORKSPACE_MARKER", encoding="utf-8"
        )
        assert original_workspace != fork_workspace
        assert (
            original_workspace / "ownership-marker.txt"
        ).read_text(encoding="utf-8") == "ORIGINAL_WORKSPACE_MARKER"
        assert (
            fork_workspace / "ownership-marker.txt"
        ).read_text(encoding="utf-8") == "FORK_WORKSPACE_MARKER"

        assert "extended-openai" in hass.data[DATA_PANELS]
        fork_panel = hass.data[DATA_PANELS]["extended-openai"]

        assert await hass.config_entries.async_unload(original.entry_id)
        await hass.async_block_till_done()
        assert original.state is ConfigEntryState.NOT_LOADED
        assert fork.state is ConfigEntryState.LOADED
        assert conversation.async_get_agent(hass, fork_conversation_id) is fork_agent
        assert hass.data[DATA_PANELS]["extended-openai"] == fork_panel
        assert (
            fork_workspace / "ownership-marker.txt"
        ).read_text(encoding="utf-8") == "FORK_WORKSPACE_MARKER"
        assert [
            item.content
            for item in await fork_memory.async_search(
                "side-by-side-owner", "FORK_SIDE_BY_SIDE_STORAGE_MARKER"
            )
        ] == ["FORK_SIDE_BY_SIDE_STORAGE_MARKER"]

        assert await hass.config_entries.async_setup(original.entry_id)
        await hass.async_block_till_done()
        assert original.state is ConfigEntryState.LOADED
        assert fork.state is ConfigEntryState.LOADED

        original_agent = conversation.async_get_agent(hass, original_conversation_id)
        assert original_agent is not None
        assert await hass.config_entries.async_unload(fork.entry_id)
        await hass.async_block_till_done()
        assert fork.state is ConfigEntryState.NOT_LOADED
        assert original.state is ConfigEntryState.LOADED
        assert conversation.async_get_agent(hass, original_conversation_id) is original_agent
        assert (
            original_workspace / "ownership-marker.txt"
        ).read_text(encoding="utf-8") == "ORIGINAL_WORKSPACE_MARKER"

        assert await hass.config_entries.async_setup(fork.entry_id)
        await hass.async_block_till_done()
        assert fork.state is ConfigEntryState.LOADED
        reloaded_memory = await async_get_memory(
            hass, fork.entry_id, fork_subentry.subentry_id
        )
        assert [
            item.content
            for item in await reloaded_memory.async_search(
                "side-by-side-owner", "FORK_SIDE_BY_SIDE_STORAGE_MARKER"
            )
        ] == ["FORK_SIDE_BY_SIDE_STORAGE_MARKER"]
        assert hass.data[DATA_PANELS]["extended-openai"] == fork_panel
    finally:
        await hass.async_stop()


def test_original_and_fork_run_side_by_side_without_cross_domain_ownership(
    tmp_path: Path,
) -> None:
    """Stage the real original release and the fork together in one HA config."""
    original_source = os.environ.get("ORIGINAL_COMPONENT_DIR")
    if not original_source:
        pytest.skip("requires the pinned original integration payload")

    original_source_path = Path(original_source).resolve()
    original_manifest = json.loads(
        (original_source_path / "manifest.json").read_text(encoding="utf-8")
    )
    assert original_manifest["domain"] == _ORIGINAL_DOMAIN
    assert original_manifest["version"] == _ORIGINAL_RELEASE

    repo_root = Path(__file__).resolve().parents[1]
    fork_source = repo_root / "custom_components" / DOMAIN
    config_dir = tmp_path / "side-by-side-config"
    custom_components = config_dir / "custom_components"
    custom_components.mkdir(parents=True)
    shutil.copytree(original_source_path, custom_components / _ORIGINAL_DOMAIN)
    shutil.copytree(
        fork_source,
        custom_components / DOMAIN,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    ensure_staged_custom_components_package(config_dir)
    (config_dir / "configuration.yaml").write_text(
        "homeassistant:\n  name: Side by Side Identity Isolation\nrecorder:\n",
        encoding="utf-8",
    )

    env = os.environ.copy()
    env[_SIDE_BY_SIDE_CHILD] = "1"
    env[_SIDE_BY_SIDE_CONFIG] = str(config_dir)
    existing_pythonpath = env.get("PYTHONPATH")
    roots = [str(config_dir.resolve()), str(repo_root.resolve())]
    if existing_pythonpath:
        roots.extend(existing_pythonpath.split(os.pathsep))
    env["PYTHONPATH"] = os.pathsep.join(dict.fromkeys(roots))

    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve())],
        cwd=config_dir,
        env=env,
        text=True,
        capture_output=True,
        timeout=180,
        check=False,
    )
    assert result.returncode == 0, (
        "side-by-side Home Assistant child failed\n"
        f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    )


if __name__ == "__main__" and os.environ.get(_SIDE_BY_SIDE_CHILD):
    asyncio.run(_side_by_side_child(Path(os.environ[_SIDE_BY_SIDE_CONFIG]).resolve()))
