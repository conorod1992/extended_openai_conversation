"""Template functions for Extended OpenAI Conversation (Responses)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any
from weakref import WeakKeyDictionary

from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant
from homeassistant.helpers.template import TemplateEnvironment

from .const import DEFAULT_WORKING_DIRECTORY, DOMAIN
from .delayed_tools import async_setup_delayed_tools
from .helpers import get_exposed_entities
from .skills import SkillManager

if TYPE_CHECKING:
    from collections.abc import Callable

_LOGGER = logging.getLogger(__name__)

DATA_TEMPLATE_MANAGER = "template_manager"

TEMPLATE_EXTENDED_OPENAI = "extended_openai"
TEMPLATE_GET_ENTITIES = "exposed_entities"
TEMPLATE_WORKING_DIRECTORY = "working_directory"
TEMPLATE_SKILL_DIR = "skill_dir"
_MISSING = object()


async def async_setup_templates(hass: HomeAssistant, entry_id: str) -> bool:
    """Set up template functions for one loaded config entry."""
    # This setup point runs after conversation entities have been forwarded and is
    # integration-global, making it a safe place to recover durable delayed calls.
    await async_setup_delayed_tools(hass)

    domain_data = hass.data.setdefault(DOMAIN, {})
    manager = domain_data.get(DATA_TEMPLATE_MANAGER)
    if manager is None:
        manager = ExtendedOpenAITemplateManager(hass)
        # Publish the manager before setup. The setup coroutine does not yield, so
        # another entry cannot observe a half-installed patch, while an exception
        # can still remove this exact instance below.
        domain_data[DATA_TEMPLATE_MANAGER] = manager
        try:
            await manager.async_setup()
        except BaseException:
            await manager.async_on_unload()
            if domain_data.get(DATA_TEMPLATE_MANAGER) is manager:
                domain_data.pop(DATA_TEMPLATE_MANAGER, None)
            raise
    manager.acquire(entry_id)
    return True


async def async_unload_templates(hass: HomeAssistant, entry_id: str) -> bool:
    """Release template functions after one config entry fully unloaded."""
    domain_data = hass.data.get(DOMAIN, {})
    manager = domain_data.get(DATA_TEMPLATE_MANAGER)
    if manager is None:
        return True

    manager.release(entry_id)
    if manager.in_use:
        return True

    await manager.async_on_unload()
    if domain_data.get(DATA_TEMPLATE_MANAGER) is manager:
        domain_data.pop(DATA_TEMPLATE_MANAGER, None)
    return True


class ExtendedOpenAITemplateManager:
    """Class to manage template functions."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the template manager."""
        self.hass = hass
        self._extended_openai = {
            TEMPLATE_GET_ENTITIES: self._get_exposed_entities,
            TEMPLATE_WORKING_DIRECTORY: self._get_working_directory,
            TEMPLATE_SKILL_DIR: self._get_skill_dir,
        }
        self._original_init: Callable[..., None] | None = None
        self._replacement_init: Callable[..., None] | None = None
        self._remove_stop_listener: Callable[[], None] | None = None
        self._environments: WeakKeyDictionary[TemplateEnvironment, Any] = (
            WeakKeyDictionary()
        )
        self._entry_ids: set[str] = set()

    @property
    def in_use(self) -> bool:
        """Return whether any successfully loaded config entry still needs globals."""
        return bool(self._entry_ids)

    def acquire(self, entry_id: str) -> None:
        """Track one config entry that completed platform and template setup."""
        self._entry_ids.add(entry_id)

    def release(self, entry_id: str) -> None:
        """Release one config entry only after its platform unload succeeded."""
        self._entry_ids.discard(entry_id)

    def _get_exposed_entities(self) -> list[dict[str, Any]]:
        return get_exposed_entities(self.hass)

    def _get_working_directory(self) -> str:
        """Get the absolute working directory path."""
        working_dir = DEFAULT_WORKING_DIRECTORY
        if Path(working_dir).is_absolute():
            return str(Path(working_dir))
        return str(Path(self.hass.config.config_dir) / working_dir)

    def _get_skill_dir(self, name: str) -> str:
        """Get the absolute directory path for a skill by name.

        Args:
            name: The skill name (e.g., 'crypto', 'skill-creator')

        Returns:
            Absolute path to the skill directory

        Raises:
            ValueError: If the skill is not found
        """
        manager = SkillManager._instance
        if manager is None:
            raise ValueError("SkillManager not initialized")
        skill = manager.get_skill(name)
        if skill is None:
            raise ValueError(f"Skill not found: {name}")
        return str(skill.path.parent)

    async def async_setup(self) -> None:
        """Set up the template functions."""
        if self._original_init is not None:
            return
        _LOGGER.debug(
            "Setting up Extended OpenAI Conversation (Responses) template functions"
        )

        # Capture an immutable delegate: a later wrapper may retain ours even
        # after unload, when it must remain a working, inactive pass-through.
        original_init = TemplateEnvironment.__init__
        self._original_init = original_init

        def template_environment_init(
            template_env_self: TemplateEnvironment,
            hass: HomeAssistant | None,
            limited: bool | None = False,
            strict: bool | None = False,
            log_fn: Callable[[int, str], None] | None = None,
        ) -> None:
            original_init(template_env_self, hass, limited, strict, log_fn)
            if (
                self._replacement_init is template_environment_init
                and hass is self.hass
            ):
                self._register_environment(template_env_self)

        self._replacement_init = template_environment_init
        TemplateEnvironment.__init__ = template_environment_init  # type: ignore[method-assign,assignment]
        for key in (
            "template.environment",
            "template.environment_limited",
            "template.environment_strict",
        ):
            if key in self.hass.data:
                self._register_environment(self.hass.data[key])
        self._remove_stop_listener = self.hass.bus.async_listen_once(
            EVENT_HOMEASSISTANT_STOP, self._async_stop
        )

    def _register_environment(self, environment: TemplateEnvironment) -> None:
        """Remember exactly what this manager replaced in each environment."""
        if environment in self._environments:
            return
        self._environments[environment] = environment.globals.get(
            TEMPLATE_EXTENDED_OPENAI, _MISSING
        )
        environment.globals[TEMPLATE_EXTENDED_OPENAI] = self._extended_openai

    async def _async_stop(self, _event: Any) -> None:
        """Release process globals even when HA stops without unloading entries."""
        await self.async_on_unload()
        domain_data = self.hass.data.get(DOMAIN, {})
        if domain_data.get(DATA_TEMPLATE_MANAGER) is self:
            domain_data.pop(DATA_TEMPLATE_MANAGER, None)

    async def async_on_unload(self) -> None:
        """Tear down the template functions."""
        _LOGGER.debug(
            "Tearing down Extended OpenAI Conversation (Responses) template functions"
        )

        self._entry_ids.clear()
        if (
            self._original_init is not None
            and TemplateEnvironment.__init__ is self._replacement_init
        ):
            TemplateEnvironment.__init__ = self._original_init  # type: ignore[method-assign]
        self._replacement_init = None
        self._original_init = None
        if self._remove_stop_listener is not None:
            self._remove_stop_listener()
            self._remove_stop_listener = None
        for environment, original in self._environments.items():
            if (
                environment.globals.get(TEMPLATE_EXTENDED_OPENAI)
                is self._extended_openai
            ):
                if original is _MISSING:
                    environment.globals.pop(TEMPLATE_EXTENDED_OPENAI, None)
                else:
                    environment.globals[TEMPLATE_EXTENDED_OPENAI] = original
        self._environments.clear()
