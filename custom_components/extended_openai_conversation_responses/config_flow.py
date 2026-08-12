"""Config flow for Extended OpenAI Conversation (Responses)."""

from __future__ import annotations

import logging
import types
from typing import Any

from openai._exceptions import APIConnectionError, AuthenticationError
import voluptuous as vol
import yaml

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.const import CONF_API_KEY, CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
)

from .agent_config import agent_config_defaults, normalize_agent_config
from .agent_test import async_test_agent
from .const import (
    API_MODE_OPTIONS,
    API_PROVIDERS,
    CONF_ADVANCED_OPTIONS,
    CONF_API_MODE,
    CONF_API_PROVIDER,
    CONF_API_VERSION,
    CONF_BASE_URL,
    CONF_CHAT_MODEL,
    CONF_MAX_TOKENS,
    CONF_ORGANIZATION,
    CONF_REASONING_EFFORT,
    CONF_SERVICE_TIER,
    CONF_SHORTEN_TOOL_CALL_ID,
    CONF_SKILLS,
    CONF_SKIP_AUTHENTICATION,
    CONF_TEMPERATURE,
    CONF_TOP_P,
    CONFIG_ENTRY_VERSION,
    DEFAULT_ADVANCED_OPTIONS,
    DEFAULT_AI_TASK_NAME,
    DEFAULT_AI_TASK_OPTIONS,
    DEFAULT_API_MODE,
    DEFAULT_API_PROVIDER,
    DEFAULT_CHAT_MODEL,
    DEFAULT_CONF_BASE_URL,
    DEFAULT_CONF_FUNCTION_TOOLS,
    DEFAULT_CONVERSATION_NAME,
    DEFAULT_MAX_TOKENS,
    DEFAULT_NAME,
    DEFAULT_REASONING_EFFORT,
    DEFAULT_SERVICE_TIER,
    DEFAULT_SHORTEN_TOOL_CALL_ID,
    DEFAULT_SKIP_AUTHENTICATION,
    DEFAULT_TEMPERATURE,
    DEFAULT_TOP_P,
    DOMAIN,
    REASONING_EFFORT_OPTIONS,
    SERVICE_TIER_OPTIONS,
)
from .helpers import get_authenticated_client, get_model_config
from .skills import SkillManager

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_NAME, default="ChatGPT"): str,
        vol.Required(CONF_API_KEY): str,
        vol.Optional(CONF_BASE_URL, default=DEFAULT_CONF_BASE_URL): str,
        vol.Optional(CONF_API_VERSION): str,
        vol.Optional(CONF_ORGANIZATION): str,
        vol.Optional(
            CONF_SKIP_AUTHENTICATION, default=DEFAULT_SKIP_AUTHENTICATION
        ): bool,
        vol.Optional(CONF_API_PROVIDER, default=DEFAULT_API_PROVIDER): SelectSelector(
            SelectSelectorConfig(
                options=[
                    SelectOptionDict(
                        value=api_provider["key"], label=api_provider["label"]
                    )
                    for api_provider in API_PROVIDERS
                ],
                mode=SelectSelectorMode.DROPDOWN,
            )
        ),
    }
)


class ExtendedOpenAIOptionsFlow(OptionsFlow):
    """Native integration UI for agent testing and management-panel discovery."""

    _test_report: str = ""

    def _agent_options(self) -> list[SelectOptionDict]:
        return [
            SelectOptionDict(value=subentry.subentry_id, label=subentry.title)
            for subentry in self.config_entry.subentries.values()
            if subentry.subentry_type == "conversation"
        ]

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show integration-owned management actions."""
        return self.async_show_menu(
            step_id="init",
            menu_options=["test_agent", "manage_memory", "manage_knowledge"],
        )

    async def async_step_manage_memory(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Point users to the authenticated memory management panel."""
        if user_input is not None:
            return await self.async_step_init()
        return self.async_show_form(
            step_id="manage_memory",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        "panel_path", default="/extended-openai-memory"
                    ): TextSelector(TextSelectorConfig(read_only=True))
                }
            ),
        )

    async def async_step_manage_knowledge(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Point users to the authenticated Knowledge Library panel."""
        if user_input is not None:
            return await self.async_step_init()
        return self.async_show_form(
            step_id="manage_knowledge",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        "panel_path", default="/extended-openai-knowledge"
                    ): TextSelector(TextSelectorConfig(read_only=True))
                }
            ),
        )

    async def async_step_test_agent(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select and safely test a configured conversation agent."""
        if user_input is not None:
            subentry = self.config_entry.subentries[user_input["agent_id"]]
            self._test_report = (
                await async_test_agent(self.hass, self.config_entry, subentry)
            ).as_text()
            return await self.async_step_test_result()
        return self.async_show_form(
            step_id="test_agent",
            data_schema=vol.Schema(
                {
                    vol.Required("agent_id"): SelectSelector(
                        SelectSelectorConfig(
                            options=self._agent_options(),
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
        )

    async def async_step_test_result(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Display the structured test result without changing configuration."""
        if user_input is not None:
            return await self.async_step_init()
        return self.async_show_form(
            step_id="test_result",
            data_schema=vol.Schema(
                {
                    vol.Optional("report", default=self._test_report): TextSelector(
                        TextSelectorConfig(multiline=True, read_only=True)
                    )
                }
            ),
        )


DEFAULT_CONF_FUNCTION_TOOLS_STR = yaml.dump(
    DEFAULT_CONF_FUNCTION_TOOLS, sort_keys=False
)

DEFAULT_OPTIONS = types.MappingProxyType(agent_config_defaults())


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> None:
    """Validate the user input allows us to connect.

    Data has the keys from STEP_USER_DATA_SCHEMA with values provided by the user.
    """
    api_key = data[CONF_API_KEY]
    base_url = data.get(CONF_BASE_URL)
    api_version = data.get(CONF_API_VERSION)
    organization = data.get(CONF_ORGANIZATION)
    skip_authentication = data.get(CONF_SKIP_AUTHENTICATION, False)
    api_provider = data.get(CONF_API_PROVIDER)

    if base_url == DEFAULT_CONF_BASE_URL:
        # Do not set base_url if using OpenAI for case of OpenAI's base_url change
        base_url = None
        data.pop(CONF_BASE_URL)

    if api_provider == "azure" and not base_url:
        raise HomeAssistantError("Azure OpenAI requires a custom base URL.")

    await get_authenticated_client(
        hass=hass,
        api_key=api_key,
        base_url=base_url,
        api_version=api_version,
        organization=organization,
        api_provider=api_provider,
        skip_authentication=skip_authentication,
    )


class ExtendedOpenAIConversationConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for OpenAI Conversation."""

    VERSION = CONFIG_ENTRY_VERSION

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the integration management flow."""
        return ExtendedOpenAIOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        if user_input is None:
            return self.async_show_form(
                step_id="user", data_schema=STEP_USER_DATA_SCHEMA
            )

        errors = {}

        try:
            await validate_input(self.hass, user_input)
        except APIConnectionError:
            errors["base"] = "cannot_connect"
        except AuthenticationError:
            errors["base"] = "invalid_auth"
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception("Unexpected exception")
            errors["base"] = "unknown"
        else:
            return self.async_create_entry(
                title=user_input.get(CONF_NAME, DEFAULT_NAME),
                data=user_input,
                subentries=[
                    {
                        "subentry_type": "conversation",
                        "data": dict(DEFAULT_OPTIONS),
                        "title": DEFAULT_CONVERSATION_NAME,
                        "unique_id": None,
                    },
                    {
                        "subentry_type": "ai_task_data",
                        "data": dict(DEFAULT_AI_TASK_OPTIONS),
                        "title": DEFAULT_AI_TASK_NAME,
                        "unique_id": None,
                    },
                ],
            )

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Return subentries supported by this integration."""
        return {
            "conversation": ExtendedOpenAISubentryFlowHandler,
            "ai_task_data": ExtendedOpenAIAITaskSubentryFlowHandler,
        }


class ExtendedOpenAISubentryFlowHandler(ConfigSubentryFlow):
    """Flow for managing OpenAI subentries."""

    options: dict[str, Any]
    _available_skills: list[dict[str, Any]] | None = None

    @property
    def _is_new(self) -> bool:
        """Return if this is a new subentry."""
        return self.source == "user"

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Add a subentry."""
        self.options = agent_config_defaults()
        return await self.async_step_init()

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Handle reconfiguration of a subentry."""
        self.options = normalize_agent_config(
            dict(self._get_reconfigure_subentry().data), reject_unknown=False
        )
        return await self.async_step_init()

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Manage the options."""
        # Keep native HA flows focused on lifecycle. Detailed behaviour is edited
        # in the full-width integration panel, which writes this same subentry.
        if self._get_entry().state != ConfigEntryState.LOADED:
            return self.async_abort(reason="entry_not_loaded")

        if user_input is not None:
            if not self._is_new:
                return self.async_abort(reason="agent_configuration_managed_in_panel")
            title = str(user_input.get(CONF_NAME, DEFAULT_CONVERSATION_NAME)).strip()
            initial = dict(self.options)
            initial[CONF_SKILLS] = [
                skill["name"] for skill in await self._async_get_skills()
            ]
            return self.async_create_entry(
                title=title or DEFAULT_CONVERSATION_NAME,
                data=normalize_agent_config(initial),
            )

        if self._is_new:
            return self.async_show_form(
                step_id="init",
                data_schema=vol.Schema(
                    {vol.Required(CONF_NAME, default=DEFAULT_CONVERSATION_NAME): str}
                ),
            )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        "management_panel",
                        default="Open Extended OpenAI in the sidebar to configure this agent.",
                    ): TextSelector(TextSelectorConfig(read_only=True))
                }
            ),
        )

    async def _async_get_skills(self) -> list[dict[str, Any]]:
        """Load available skills using SkillManager."""
        skill_manager = await SkillManager.async_get_instance(self.hass)
        return [
            {
                "name": skill.name,
                "description": skill.description,
            }
            for skill in skill_manager.get_all_skills()
        ]


class ExtendedOpenAIAITaskSubentryFlowHandler(ConfigSubentryFlow):
    """Flow for managing AI Task subentries."""

    options: dict[str, Any]
    _temp_data: dict[str, Any] | None = None

    @property
    def _is_new(self) -> bool:
        """Return if this is a new subentry."""
        return self.source == "user"

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Add a subentry."""
        self.options = dict(DEFAULT_AI_TASK_OPTIONS)
        return await self.async_step_init()

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Handle reconfiguration of a subentry."""
        self.options = dict(self._get_reconfigure_subentry().data)
        return await self.async_step_init()

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Manage the options."""
        # Abort if entry is not loaded
        if self._get_entry().state != ConfigEntryState.LOADED:
            return self.async_abort(reason="entry_not_loaded")

        if user_input is not None:
            # Check if advanced options is enabled
            if user_input.get(CONF_ADVANCED_OPTIONS, False):
                # Store data and move to advanced step
                self._temp_data = user_input
                return await self.async_step_advanced()

            # No advanced options, save directly
            if self._is_new:
                title = user_input.get(CONF_NAME, DEFAULT_AI_TASK_NAME)
                if CONF_NAME in user_input:
                    del user_input[CONF_NAME]
                return self.async_create_entry(
                    title=title,
                    data=user_input,
                )
            return self.async_update_and_abort(
                self._get_entry(),
                self._get_reconfigure_subentry(),
                data=user_input,
            )

        schema: dict = {}

        if self._is_new:
            schema[vol.Optional(CONF_NAME, default=DEFAULT_AI_TASK_NAME)] = str

        schema.update(
            {
                vol.Optional(
                    CONF_CHAT_MODEL,
                    default=DEFAULT_CHAT_MODEL,
                ): str,
                vol.Optional(
                    CONF_API_MODE,
                    default=DEFAULT_API_MODE,
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            SelectOptionDict(value=mode["key"], label=mode["label"])
                            for mode in API_MODE_OPTIONS
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Optional(
                    CONF_MAX_TOKENS,
                    default=DEFAULT_MAX_TOKENS,
                ): int,
                vol.Optional(
                    CONF_ADVANCED_OPTIONS,
                    default=DEFAULT_ADVANCED_OPTIONS,
                ): BooleanSelector(),
            }
        )

        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(schema), self.options
            ),
        )

    async def async_step_advanced(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Handle advanced options step."""
        if user_input is not None:
            # Merge advanced options with temp data
            final_data = {**(self._temp_data or {}), **user_input}

            if self._is_new:
                title = final_data.get(CONF_NAME, DEFAULT_AI_TASK_NAME)
                final_data.pop(CONF_NAME, None)
                return self.async_create_entry(
                    title=title,
                    data=final_data,
                )
            return self.async_update_and_abort(
                self._get_entry(),
                self._get_reconfigure_subentry(),
                data=final_data,
            )

        # Build schema for advanced options based on selected model
        chat_model = (self._temp_data or {}).get(CONF_CHAT_MODEL, DEFAULT_CHAT_MODEL)
        model_config = get_model_config(chat_model)

        schema: dict[Any, Any] = {}

        # Add top_p if supported
        if model_config["supports_top_p"]:
            schema[
                vol.Optional(
                    CONF_TOP_P,
                    default=DEFAULT_TOP_P,
                )
            ] = NumberSelector(NumberSelectorConfig(min=0, max=1, step=0.05))

        # Add temperature if supported
        if model_config["supports_temperature"]:
            schema[
                vol.Optional(
                    CONF_TEMPERATURE,
                    default=DEFAULT_TEMPERATURE,
                )
            ] = NumberSelector(NumberSelectorConfig(min=0, max=2, step=0.05))

        # Add reasoning_effort if supported (o1, o3, o4, gpt-5 models)
        if model_config.get("supports_reasoning_effort"):
            schema[
                vol.Optional(
                    CONF_REASONING_EFFORT,
                    default=DEFAULT_REASONING_EFFORT,
                )
            ] = SelectSelector(
                SelectSelectorConfig(
                    options=[
                        SelectOptionDict(value=opt, label=opt.capitalize())
                        for opt in REASONING_EFFORT_OPTIONS
                    ],
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )

        # Add service_tier if supported (o3, o4, gpt-5 models)
        if model_config.get("supports_service_tier"):
            schema[
                vol.Optional(
                    CONF_SERVICE_TIER,
                    default=DEFAULT_SERVICE_TIER,
                )
            ] = SelectSelector(
                SelectSelectorConfig(
                    options=[
                        SelectOptionDict(value=opt, label=opt.capitalize())
                        for opt in SERVICE_TIER_OPTIONS
                    ],
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )

        # Add shorten_tool_call_id option (for Mistral AI compatibility)
        schema[
            vol.Optional(
                CONF_SHORTEN_TOOL_CALL_ID,
                default=DEFAULT_SHORTEN_TOOL_CALL_ID,
            )
        ] = BooleanSelector()

        return self.async_show_form(
            step_id="advanced",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(schema), self.options
            ),
        )
