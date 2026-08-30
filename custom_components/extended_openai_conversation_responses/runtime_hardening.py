"""Runtime lifecycle and model-facing resource hardening."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import asdict
import logging
from pathlib import Path
import sys
from typing import Any, cast

from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

MAX_MODEL_TOOL_RESULT_CHARACTERS = 32_000
_TOOL_RESULT_TRUNCATION_LABEL = "...[tool result truncated]"
_USAGE_GETTER_LOCKS = f"{DOMAIN}.usage_getter_locks"
_SKILL_MANAGER_INIT_LOCK = f"{DOMAIN}.skill_manager_init_lock"
_INSTALLED = False


def bounded_tool_result_text(value: Any) -> str:
    """Return a deterministic bounded representation for the next model request."""
    text = value if isinstance(value, str) else str(value)
    if len(text) <= MAX_MODEL_TOOL_RESULT_CHARACTERS:
        return text
    suffix = f"\n{_TOOL_RESULT_TRUNCATION_LABEL} original_characters={len(text)}"
    return text[: max(0, MAX_MODEL_TOOL_RESULT_CHARACTERS - len(suffix))] + suffix


def install_runtime_hardening() -> None:
    """Install one-time reliability guards on integration-owned runtime seams."""
    global _INSTALLED
    if _INSTALLED:
        return
    _install_usage_hardening()
    _install_skill_hardening()
    _install_guest_mode_hardening()
    _install_tool_result_hardening()
    _INSTALLED = True


def _manager_lock(manager: Any, attribute: str) -> asyncio.Lock:
    lock = getattr(manager, attribute, None)
    if not isinstance(lock, asyncio.Lock):
        lock = asyncio.Lock()
        manager.__dict__[attribute] = lock
    return lock


def _install_usage_hardening() -> None:
    """Serialize first-use Usage initialization and leave failures retryable."""
    from . import usage as usage_module

    manager_type = usage_module.UsageManager
    current_initialize = manager_type.async_initialize
    if not getattr(current_initialize, "_extended_openai_init_guard", False):
        original_initialize = current_initialize

        async def async_initialize(manager: Any) -> None:
            lock = _manager_lock(manager, "_extended_openai_initialize_lock")
            async with lock:
                if manager._initialized:
                    return
                try:
                    await original_initialize(manager)
                except Exception:
                    # The original loader appends retained detail records as it goes.
                    # A partial load must never leak into a later retry.
                    manager.totals = usage_module.UsageTotals()
                    manager.daily = {}
                    manager.requests = []
                    manager.runs = []
                    manager._run_started = {}
                    manager._initialized = False
                    raise

        async_initialize._extended_openai_init_guard = True  # type: ignore[attr-defined]
        manager_type.async_initialize = async_initialize  # type: ignore[method-assign,assignment]

    current_getter = usage_module.async_get_usage
    if not getattr(current_getter, "_extended_openai_getter_guard", False):
        original_getter = current_getter

        async def async_get_usage(
            hass: HomeAssistant, entry_id: str, subentry_id: str
        ) -> Any:
            locks: dict[tuple[str, str], asyncio.Lock] = hass.data.setdefault(
                _USAGE_GETTER_LOCKS, {}
            )
            key = (entry_id, subentry_id)
            lock = locks.setdefault(key, asyncio.Lock())
            async with lock:
                return await original_getter(hass, entry_id, subentry_id)

        async_get_usage._extended_openai_getter_guard = True  # type: ignore[attr-defined]
        usage_module.async_get_usage = async_get_usage

        # ``management_ui`` and ``backup`` are imported before async_setup runs.
        # Replace any already-bound aliases; future imports naturally see the
        # guarded function in the usage module.
        package_prefix = f"{__package__}."
        for module_name, module in tuple(sys.modules.items()):
            if (
                module is not None
                and module_name.startswith(package_prefix)
                and module.__dict__.get("async_get_usage") is original_getter
            ):
                module.__dict__["async_get_usage"] = async_get_usage


def _install_skill_hardening() -> None:
    """Make first skill discovery awaitable by every caller and reload atomically."""
    from .skills import SkillManager, SkillMdParser

    current_load = SkillManager.async_load_skills
    if not getattr(current_load, "_extended_openai_atomic_load", False):

        async def async_load_skills(manager: SkillManager) -> None:
            lock = _manager_lock(manager, "_extended_openai_skill_load_lock")
            async with lock:
                skills_data = await manager._hass.async_add_executor_job(
                    manager._load_skills_from_dir_sync, manager.user_skills_dir
                )
                loaded: dict[str, Any] = {}
                for skill_path, content in skills_data:
                    try:
                        skill = SkillMdParser.parse(
                            content, skill_path, manager.user_skills_dir
                        )
                        if skill is not None:
                            loaded[skill.name] = skill
                    except Exception:
                        _LOGGER.exception(
                            "Unexpected error loading skill from %s", skill_path
                        )
                # Publish only a complete discovery result. A failed executor read
                # leaves the previous catalogue untouched.
                manager._skills = loaded
                manager.__dict__["_extended_openai_skills_initialized"] = True
                _LOGGER.info("Loaded %d skills", len(loaded))

        async_load_skills._extended_openai_atomic_load = True  # type: ignore[attr-defined]
        SkillManager.async_load_skills = async_load_skills  # type: ignore[method-assign,assignment]

    current_get = SkillManager.async_get_instance
    if not getattr(current_get, "_extended_openai_init_guard", False):

        async def async_get_instance(
            cls: type[SkillManager],
            hass: HomeAssistant,
            user_skills_dir: str | None = None,
        ) -> SkillManager:
            lock = hass.data.setdefault(_SKILL_MANAGER_INIT_LOCK, asyncio.Lock())
            async with lock:
                manager = cls._instance
                if manager is None or manager._hass is not hass:
                    manager = cls(hass)
                    cls._instance = manager
                    if user_skills_dir:
                        manager._user_skills_dir = Path(user_skills_dir)
                elif (
                    user_skills_dir
                    and manager._user_skills_dir is None
                    and not getattr(
                        manager, "_extended_openai_skills_initialized", False
                    )
                ):
                    manager._user_skills_dir = Path(user_skills_dir)

                if getattr(manager, "_extended_openai_skills_initialized", False):
                    return manager
                try:
                    await manager.async_load_skills()
                except Exception:
                    if cls._instance is manager:
                        cls._instance = None
                    raise
                return manager

        async_get_instance._extended_openai_init_guard = True  # type: ignore[attr-defined]
        SkillManager.async_get_instance = classmethod(async_get_instance)  # type: ignore[method-assign,assignment]

    current_loaded = SkillManager.get_loaded_instance
    if not getattr(current_loaded, "_extended_openai_loaded_guard", False):

        def get_loaded_instance(cls: type[SkillManager]) -> SkillManager | None:
            manager = cls._instance
            if manager is None or not getattr(
                manager, "_extended_openai_skills_initialized", False
            ):
                return None
            return manager

        get_loaded_instance._extended_openai_loaded_guard = True  # type: ignore[attr-defined]
        SkillManager.get_loaded_instance = classmethod(get_loaded_instance)  # type: ignore[method-assign,assignment]


def _install_guest_mode_hardening() -> None:
    """Keep Guest Mode state serialized, durable, and retryable on Store failures."""
    from . import guest_mode as guest_module

    manager_type = guest_module.GuestModeManager
    current_initialize = manager_type.async_initialize
    if getattr(current_initialize, "_extended_openai_guest_guard", False):
        return

    original_restrict = manager_type.async_restrict
    original_update = manager_type.async_update_trusted

    async def async_initialize(manager: Any) -> None:
        lock = _manager_lock(manager, "_extended_openai_guest_state_lock")
        async with lock:
            if manager._initialized:
                return
            # Storage I/O failures are not malformed state. Propagate them so a
            # later getter/setup can retry instead of silently disabling Guest Mode.
            data = await manager._store.async_load()
            raw = data.get("schedule") if isinstance(data, Mapping) else None
            schedule = None
            if isinstance(raw, Mapping):
                try:
                    candidate = guest_module.GuestModeSchedule(**dict(raw))
                    guest_module._parse_timestamp(
                        manager.hass, candidate.active_from, "active_from"
                    )
                    if candidate.active_until is not None:
                        guest_module._parse_timestamp(
                            manager.hass, candidate.active_until, "active_until"
                        )
                    schedule = candidate
                except TypeError, ValueError:
                    _LOGGER.warning(
                        "Ignoring malformed Guest Mode state", exc_info=True
                    )
            manager._schedule = schedule
            manager._initialized = True

    async def _async_set(
        manager: Any, start: Any, end: Any | None, source: str
    ) -> None:
        updated = guest_module.dt_util.utcnow().isoformat()
        schedule = guest_module.GuestModeSchedule(
            active_from=guest_module._as_utc(start).isoformat(),
            active_until=(
                guest_module._as_utc(end).isoformat() if end is not None else None
            ),
            source=source,
            updated_at=updated,
        )
        # Persist before publishing the new policy in memory.
        await manager._store.async_save({"schedule": asdict(schedule)})
        manager._schedule = schedule
        manager._notify()

    async def async_restrict(manager: Any, **kwargs: Any) -> dict[str, Any]:
        lock = _manager_lock(manager, "_extended_openai_guest_state_lock")
        async with lock:
            return await original_restrict(manager, **kwargs)

    async def async_update_trusted(manager: Any, **kwargs: Any) -> dict[str, Any]:
        lock = _manager_lock(manager, "_extended_openai_guest_state_lock")
        async with lock:
            return await original_update(manager, **kwargs)

    async def async_disable_trusted(manager: Any) -> dict[str, Any]:
        lock = _manager_lock(manager, "_extended_openai_guest_state_lock")
        async with lock:
            await manager._store.async_save({"schedule": None})
            manager._schedule = None
            manager._notify()
            return cast(dict[str, Any], manager.status())

    async def async_replace_backup(manager: Any, schedule: Any) -> None:
        lock = _manager_lock(manager, "_extended_openai_guest_state_lock")
        async with lock:
            await manager._store.async_save(
                {"schedule": asdict(schedule) if schedule is not None else None}
            )
            manager._schedule = schedule
            manager._notify()

    async_initialize._extended_openai_guest_guard = True  # type: ignore[attr-defined]
    manager_type.async_initialize = async_initialize  # type: ignore[method-assign,assignment]
    manager_type._async_set = _async_set  # type: ignore[method-assign,assignment]
    manager_type.async_restrict = async_restrict  # type: ignore[method-assign,assignment]
    manager_type.async_update_trusted = async_update_trusted  # type: ignore[method-assign,assignment]
    manager_type.async_disable_trusted = async_disable_trusted  # type: ignore[method-assign,assignment]
    manager_type.async_replace_backup = async_replace_backup  # type: ignore[method-assign,assignment]


def _install_tool_result_hardening() -> None:
    """Bound every conversation tool result after delayed-tool wrapping is installed."""
    from . import delayed_tools

    current_install = delayed_tools._install_execution_hook
    if not getattr(current_install, "_extended_openai_result_install_guard", False):
        original_install = current_install

        def install_execution_hook() -> None:
            original_install()
            _wrap_conversation_tool_results()

        install_execution_hook._extended_openai_result_install_guard = True  # type: ignore[attr-defined]
        delayed_tools._install_execution_hook = install_execution_hook

    # Tests or reload paths may already have imported the conversation platform.
    if f"{__package__}.conversation" in sys.modules:
        _wrap_conversation_tool_results()


def _wrap_conversation_tool_results() -> None:
    """Install the outermost result bound on the conversation-agent tool seam."""
    from .conversation import ExtendedOpenAIAgentEntity

    current = ExtendedOpenAIAgentEntity._execute_function_tool
    if getattr(current, "_extended_openai_tool_result_guard", False):
        return
    original = current

    async def execute_function_tool(
        entity: Any,
        function_tool: dict[str, Any],
        tool_input: Any,
        llm_context: Any,
        exposed_entities: list[dict[str, Any]],
    ) -> Any:
        content = await original(
            entity, function_tool, tool_input, llm_context, exposed_entities
        )
        payload = getattr(content, "tool_result", None)
        if isinstance(payload, dict) and isinstance(payload.get("result"), str):
            payload["result"] = bounded_tool_result_text(payload["result"])
        return content

    execute_function_tool._extended_openai_tool_result_guard = True  # type: ignore[attr-defined]
    ExtendedOpenAIAgentEntity._execute_function_tool = execute_function_tool  # type: ignore[method-assign,assignment]
