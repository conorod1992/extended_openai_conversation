"""Regression coverage for the follow-up reliability/resource hardening."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from custom_components.extended_openai_conversation_responses import usage as usage_module
from custom_components.extended_openai_conversation_responses.guest_mode import (
    GuestModeManager,
    GuestModeSchedule,
)
from custom_components.extended_openai_conversation_responses.persistence_hardening import (
    install_persistence_transactions,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    DEFAULT_MATCHING,
    RequestRules,
)
from custom_components.extended_openai_conversation_responses.runtime_hardening import (
    MAX_MODEL_TOOL_RESULT_CHARACTERS,
    bounded_tool_result_text,
    install_runtime_hardening,
)
from custom_components.extended_openai_conversation_responses.skills import SkillManager
from custom_components.extended_openai_conversation_responses.usage import (
    UsageManager,
    UsageRequest,
    UsageRun,
)


class ToggleStorage:
    """Small async storage double with controllable load/save failures."""

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        self.data = deepcopy(data)
        self.fail_loads = 0
        self.fail_saves = False
        self.loads = 0

    async def async_load(self) -> dict[str, Any] | None:
        self.loads += 1
        await asyncio.sleep(0)
        if self.fail_loads:
            self.fail_loads -= 1
            raise RuntimeError("simulated Store load failure")
        return deepcopy(self.data)

    async def async_save(self, data: dict[str, Any]) -> None:
        if self.fail_saves:
            raise RuntimeError("simulated Store save failure")
        self.data = deepcopy(data)


async def test_usage_initialization_is_serialized_and_retry_clean() -> None:
    """Concurrent/retried initialization cannot duplicate retained usage detail."""
    install_runtime_hardening()
    now = dt_util.utcnow().isoformat()
    totals = ToggleStorage({"totals": {"api_request_count": 1}})
    details = ToggleStorage(
        {
            "requests": [
                asdict(
                    UsageRequest(
                        request_id="request-1",
                        run_id="run-1",
                        timestamp=now,
                        agent_subentry_id="agent-1",
                        provider="openai",
                        model="model",
                        api_mode="responses",
                        successful=True,
                        duration_ms=10,
                    )
                )
            ],
            "runs": [
                asdict(
                    UsageRun(
                        run_id="run-1",
                        started_at=now,
                        completed_at=now,
                        duration_ms=10,
                        agent_subentry_id="agent-1",
                        home_assistant_conversation_id=None,
                        source_device_id=None,
                    )
                )
            ],
        }
    )
    manager = UsageManager(totals, detail_storage=details, agent_subentry_id="agent-1")

    await asyncio.gather(manager.async_initialize(), manager.async_initialize())
    assert totals.loads == 1
    assert details.loads == 1
    assert [item.request_id for item in manager.requests] == ["request-1"]
    assert [item.run_id for item in manager.runs] == ["run-1"]

    failed_details = ToggleStorage(details.data)
    failed_details.fail_loads = 1
    retry = UsageManager(
        ToggleStorage({"totals": {"api_request_count": 1}}),
        detail_storage=failed_details,
        agent_subentry_id="agent-1",
    )
    with pytest.raises(RuntimeError, match="simulated Store load failure"):
        await retry.async_initialize()
    assert retry.requests == []
    assert retry.runs == []
    assert retry.totals.api_request_count == 0

    await retry.async_initialize()
    assert [item.request_id for item in retry.requests] == ["request-1"]
    assert [item.run_id for item in retry.runs] == ["run-1"]


async def test_usage_getter_returns_one_manager_during_concurrent_first_use(
    hass, monkeypatch
) -> None:
    """The first two callers cannot initialize separate UsageManager instances."""
    install_runtime_hardening()
    calls = 0

    async def slow_load(_store: Store) -> None:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return None

    monkeypatch.setattr(Store, "async_load", slow_load)
    first, second = await asyncio.gather(
        usage_module.async_get_usage(hass, "entry-race", "subentry-race"),
        usage_module.async_get_usage(hass, "entry-race", "subentry-race"),
    )

    assert first is second
    assert calls == 3  # totals, daily, and detail Stores exactly once each


class SkillHass:
    """Minimal Home Assistant double for deterministic Skill discovery races."""

    def __init__(self, config_dir: Path, skill_path: Path, content: str) -> None:
        self.data: dict[str, Any] = {}
        self.config = SimpleNamespace(config_dir=str(config_dir))
        self.skill_path = skill_path
        self.content = content
        self.calls = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.fail = False

    async def async_add_executor_job(self, _target: Any, *_args: Any) -> Any:
        self.calls += 1
        self.started.set()
        await self.release.wait()
        if self.fail:
            raise RuntimeError("simulated skill discovery failure")
        return [(self.skill_path, self.content)]


async def test_skill_first_load_waits_and_failed_reload_keeps_catalog(tmp_path) -> None:
    """No caller sees a half-loaded catalogue and reload failure is non-destructive."""
    install_runtime_hardening()
    SkillManager._instance = None
    skills_dir = tmp_path / "skills"
    skill_path = skills_dir / "demo" / "SKILL.md"
    hass = SkillHass(
        tmp_path,
        skill_path,
        "---\ndescription: Demo skill\n---\nUseful instructions",
    )

    first_task = asyncio.create_task(
        SkillManager.async_get_instance(hass, user_skills_dir=str(skills_dir))
    )
    await hass.started.wait()
    assert SkillManager.get_loaded_instance() is None
    second_task = asyncio.create_task(
        SkillManager.async_get_instance(hass, user_skills_dir=str(skills_dir))
    )
    await asyncio.sleep(0)
    assert not second_task.done()

    hass.release.set()
    first, second = await asyncio.gather(first_task, second_task)
    assert first is second
    assert hass.calls == 1
    assert [skill.name for skill in first.get_all_skills()] == ["demo"]
    assert SkillManager.get_loaded_instance() is first

    hass.fail = True
    with pytest.raises(RuntimeError, match="simulated skill discovery failure"):
        await first.async_load_skills()
    assert [skill.name for skill in first.get_all_skills()] == ["demo"]
    SkillManager._instance = None


async def test_skill_initial_failure_clears_singleton_for_retry(tmp_path) -> None:
    """A failed first discovery does not publish a permanently broken singleton."""
    install_runtime_hardening()
    SkillManager._instance = None
    skills_dir = tmp_path / "skills"
    hass = SkillHass(
        tmp_path,
        skills_dir / "demo" / "SKILL.md",
        "---\ndescription: Demo skill\n---\nUseful instructions",
    )
    hass.fail = True
    hass.release.set()

    with pytest.raises(RuntimeError, match="simulated skill discovery failure"):
        await SkillManager.async_get_instance(hass, user_skills_dir=str(skills_dir))
    assert SkillManager._instance is None

    hass.fail = False
    manager = await SkillManager.async_get_instance(
        hass, user_skills_dir=str(skills_dir)
    )
    assert SkillManager.get_loaded_instance() is manager
    SkillManager._instance = None


def _guest_manager(store: ToggleStorage) -> GuestModeManager:
    manager = object.__new__(GuestModeManager)
    manager.hass = SimpleNamespace(config=SimpleNamespace(time_zone="UTC"))
    manager._store = store
    manager._schedule = None
    manager._listeners = set()
    manager._initialized = False
    return manager


async def test_guest_mode_store_failure_retries_without_fail_open() -> None:
    """Transient load errors are retried rather than converted to inactive state."""
    install_runtime_hardening()
    now = dt_util.utcnow()
    schedule = GuestModeSchedule(
        active_from=(now - timedelta(minutes=1)).isoformat(),
        active_until=None,
        source="home_assistant",
        updated_at=now.isoformat(),
    )
    store = ToggleStorage({"schedule": asdict(schedule)})
    store.fail_loads = 1
    manager = _guest_manager(store)

    with pytest.raises(RuntimeError, match="simulated Store load failure"):
        await manager.async_initialize()
    assert manager._initialized is False
    assert manager.schedule is None

    await manager.async_initialize()
    assert manager.schedule == schedule
    assert manager.is_active(now) is True


async def test_guest_mode_failed_disable_keeps_last_durable_policy() -> None:
    """A failed Store write cannot publish an unsaved Guest Mode relaxation."""
    install_runtime_hardening()
    now = dt_util.utcnow()
    schedule = GuestModeSchedule(
        active_from=(now - timedelta(minutes=1)).isoformat(),
        active_until=None,
        source="home_assistant",
        updated_at=now.isoformat(),
    )
    store = ToggleStorage({"schedule": asdict(schedule)})
    manager = _guest_manager(store)
    await manager.async_initialize()
    store.fail_saves = True

    with pytest.raises(RuntimeError, match="simulated Store save failure"):
        await manager.async_disable_trusted()
    assert manager.schedule == schedule
    assert manager.is_active(now) is True


async def test_request_rule_save_failure_rolls_back_live_configuration() -> None:
    """Request Rules expose only the last successfully persisted configuration."""
    install_persistence_transactions()
    storage = ToggleStorage()
    manager = RequestRules(storage)  # type: ignore[arg-type]
    await manager.async_initialize()
    before = manager.snapshot()["defaults"]

    changed = dict(DEFAULT_MATCHING)
    changed["fuzzy"] = not changed["fuzzy"]
    storage.fail_saves = True
    with pytest.raises(RuntimeError, match="simulated Store save failure"):
        await manager.async_set_defaults(changed)

    assert manager.snapshot()["defaults"] == before


def test_model_facing_tool_results_are_bounded_after_delayed_hook() -> None:
    """The outer conversation seam remains bounded even after delay wrapping."""
    install_runtime_hardening()
    from custom_components.extended_openai_conversation_responses import delayed_tools
    from custom_components.extended_openai_conversation_responses.conversation import (
        ExtendedOpenAIAgentEntity,
    )

    delayed_tools._install_execution_hook()
    assert getattr(
        ExtendedOpenAIAgentEntity._execute_function_tool,
        "_extended_openai_tool_result_guard",
        False,
    )

    value = "x" * (MAX_MODEL_TOOL_RESULT_CHARACTERS + 5_000)
    bounded = bounded_tool_result_text(value)
    assert len(bounded) <= MAX_MODEL_TOOL_RESULT_CHARACTERS
    assert bounded.startswith("x" * 100)
    assert "[tool result truncated]" in bounded
    assert f"original_characters={len(value)}" in bounded
