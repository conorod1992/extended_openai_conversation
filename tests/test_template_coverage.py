"""Focused coverage for template manager lifecycle and helper edges."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import template as template_module
from custom_components.extended_openai_conversation_responses.const import DOMAIN


class _FakeBus:
    def __init__(self) -> None:
        self.callback = None
        self.remove_calls = 0

    def async_listen_once(self, _event, callback):
        self.callback = callback

        def remove() -> None:
            self.remove_calls += 1

        return remove


class _FakeTemplateEnvironment:
    def __init__(self, hass, limited=False, strict=False, log_fn=None) -> None:
        self.hass = hass
        self.limited = limited
        self.strict = strict
        self.log_fn = log_fn
        self.globals: dict[str, object] = {}


def _hass() -> SimpleNamespace:
    return SimpleNamespace(
        data={},
        config=SimpleNamespace(config_dir="/config"),
        bus=_FakeBus(),
    )


@pytest.mark.asyncio
async def test_setup_failure_cleans_up_published_manager(monkeypatch) -> None:
    """A failed first setup must not leave a half-installed global manager."""
    hass = _hass()
    failed_manager = SimpleNamespace(
        async_setup=AsyncMock(side_effect=RuntimeError("setup failed")),
        async_on_unload=AsyncMock(),
        acquire=lambda _entry_id: pytest.fail("failed setup must not acquire entry"),
    )

    async def delayed_tools(_hass) -> None:
        return None

    monkeypatch.setattr(template_module, "async_setup_delayed_tools", delayed_tools)
    monkeypatch.setattr(
        template_module,
        "ExtendedOpenAITemplateManager",
        lambda _hass: failed_manager,
    )

    with pytest.raises(RuntimeError, match="setup failed"):
        await template_module.async_setup_templates(hass, "entry-1")

    failed_manager.async_on_unload.assert_awaited_once()
    assert template_module.DATA_TEMPLATE_MANAGER not in hass.data[DOMAIN]


@pytest.mark.asyncio
async def test_unload_without_manager_is_noop() -> None:
    """Unloading after failed/partial setup remains harmless."""
    assert await template_module.async_unload_templates(_hass(), "entry-1") is True


@pytest.mark.asyncio
async def test_unload_keeps_manager_while_another_entry_uses_it() -> None:
    """Shared template globals stay installed until the final entry releases them."""
    hass = _hass()

    class SharedManager:
        def __init__(self) -> None:
            self.entries = {"entry-1", "entry-2"}
            self.async_on_unload = AsyncMock()

        @property
        def in_use(self) -> bool:
            return bool(self.entries)

        def release(self, entry_id: str) -> None:
            self.entries.discard(entry_id)

    manager = SharedManager()
    hass.data[DOMAIN] = {template_module.DATA_TEMPLATE_MANAGER: manager}

    assert await template_module.async_unload_templates(hass, "entry-1") is True
    assert manager.entries == {"entry-2"}
    manager.async_on_unload.assert_not_awaited()
    assert hass.data[DOMAIN][template_module.DATA_TEMPLATE_MANAGER] is manager


@pytest.mark.asyncio
async def test_final_unload_removes_manager_only_if_still_current() -> None:
    """Teardown must not remove a newer manager installed during an unload."""
    hass = _hass()
    replacement = object()

    class ReplacedManager:
        in_use = False

        def release(self, _entry_id: str) -> None:
            return None

        async def async_on_unload(self) -> None:
            hass.data[DOMAIN][template_module.DATA_TEMPLATE_MANAGER] = replacement

    manager = ReplacedManager()
    hass.data[DOMAIN] = {template_module.DATA_TEMPLATE_MANAGER: manager}

    assert await template_module.async_unload_templates(hass, "entry-1") is True
    assert hass.data[DOMAIN][template_module.DATA_TEMPLATE_MANAGER] is replacement


def test_working_directory_preserves_absolute_default(monkeypatch) -> None:
    """An absolute configured default must not be prefixed by HA's config dir."""
    hass = _hass()
    manager = template_module.ExtendedOpenAITemplateManager(hass)
    absolute = Path("/var/lib/extended-openai/work")
    monkeypatch.setattr(template_module, "DEFAULT_WORKING_DIRECTORY", str(absolute))

    assert manager._get_working_directory() == str(absolute)


@pytest.mark.parametrize(
    ("skill_manager", "message"),
    [
        (None, "SkillManager not initialized"),
        (SimpleNamespace(get_skill=lambda _name: None), "Skill not found: missing"),
    ],
)
def test_skill_directory_reports_unavailable_skill_state(
    monkeypatch, skill_manager, message
) -> None:
    """Skill template helper gives explicit errors for both unavailable states."""
    monkeypatch.setattr(template_module.SkillManager, "_instance", skill_manager)
    manager = template_module.ExtendedOpenAITemplateManager(_hass())

    with pytest.raises(ValueError, match=message):
        manager._get_skill_dir("missing")


@pytest.mark.asyncio
async def test_environment_patch_restores_existing_global_and_is_idempotent(monkeypatch) -> None:
    """Setup patches once and unload restores exactly what an environment had before."""
    hass = _hass()
    original_value = object()
    environment = _FakeTemplateEnvironment(hass)
    environment.globals[template_module.TEMPLATE_EXTENDED_OPENAI] = original_value
    hass.data["template.environment"] = environment
    # The same object under another HA cache key also exercises duplicate registration.
    hass.data["template.environment_limited"] = environment
    monkeypatch.setattr(template_module, "TemplateEnvironment", _FakeTemplateEnvironment)

    manager = template_module.ExtendedOpenAITemplateManager(hass)
    await manager.async_setup()
    installed_init = _FakeTemplateEnvironment.__init__
    await manager.async_setup()

    assert _FakeTemplateEnvironment.__init__ is installed_init
    assert environment.globals[template_module.TEMPLATE_EXTENDED_OPENAI] is manager._extended_openai

    await manager.async_on_unload()

    assert environment.globals[template_module.TEMPLATE_EXTENDED_OPENAI] is original_value
    assert hass.bus.remove_calls == 1


@pytest.mark.asyncio
async def test_retained_wrapper_is_inactive_after_unload(monkeypatch) -> None:
    """A retained constructor wrapper becomes a plain pass-through after teardown."""
    hass = _hass()
    monkeypatch.setattr(template_module, "TemplateEnvironment", _FakeTemplateEnvironment)
    original_init = _FakeTemplateEnvironment.__init__
    manager = template_module.ExtendedOpenAITemplateManager(hass)

    await manager.async_setup()
    retained_wrapper = _FakeTemplateEnvironment.__init__
    assert retained_wrapper is not original_init

    await manager.async_on_unload()
    assert _FakeTemplateEnvironment.__init__ is original_init

    environment = object.__new__(_FakeTemplateEnvironment)
    retained_wrapper(environment, hass)

    assert environment.hass is hass
    assert template_module.TEMPLATE_EXTENDED_OPENAI not in environment.globals
    assert not manager._environments
