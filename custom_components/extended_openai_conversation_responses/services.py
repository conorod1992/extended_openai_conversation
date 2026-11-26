"""Services for the extended openai conversation component."""

import base64
from collections.abc import Mapping
import logging
import mimetypes
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

from openai._exceptions import OpenAIError
import voluptuous as vol

from homeassistant.components import conversation
from homeassistant.components.conversation import ConversationInput
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_API_KEY
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import (
    config_validation as cv,
    entity_registry as er,
    selector,
)
from homeassistant.helpers.typing import ConfigType

from . import function_configuration_mutations as function_mutations
from .agent_maintenance import get_agent_maintenance_gate
from .const import (
    API_MODE_OPTIONS,
    API_MODE_RESPONSES,
    API_PROVIDERS,
    CONF_API_MODE,
    CONF_API_PROVIDER,
    CONF_API_VERSION,
    CONF_BASE_URL,
    CONF_CHAT_MODEL,
    CONF_ORGANIZATION,
    CONF_SKIP_AUTHENTICATION,
    DEFAULT_API_MODE,
    DOMAIN,
    SERVICE_CALL_FUNCTION,
    SERVICE_DISABLE_FUNCTION_TOOLS,
    SERVICE_ENABLE_FUNCTION_TOOLS,
    SERVICE_GUEST_MODE_DISABLE,
    SERVICE_GUEST_MODE_UPDATE,
    SERVICE_MEMORY_CLEAR,
    SERVICE_MEMORY_DELETE,
    SERVICE_MEMORY_LIST,
    SERVICE_PROCESS,
    SERVICE_QUERY_IMAGE,
    SERVICE_REBUILD_MEMORY_EMBEDDINGS,
    SERVICE_RELOAD_SKILLS,
)
from .guest_mode import async_get_guest_mode
from .ha_schema import schema_for_ha
from .helpers import get_api_mode, get_authenticated_client, is_native_openai_connection
from .live_subentry_updates import update_live_subentry
from .memory import async_get_memory, memory_as_dict, memory_user_id
from .model_capabilities import model_capability_snapshot
from .provider_errors import (
    ensure_successful_responses_result,
    log_provider_failure,
    provider_user_message,
    request_reauthentication,
)
from .request import (
    async_capacity_ha_snapshot,
    query_image_request_settings,
    request_model_capabilities,
    validate_runtime_readiness,
)
from .request_rules import async_call_active_function
from .resource_limits import MAX_ATTACHMENT_COUNT, read_bounded_local_file

SERVICE_ENABLE_FUNCTION_GROUPS = "enable_function_groups"
SERVICE_DISABLE_FUNCTION_GROUPS = "disable_function_groups"

QUERY_IMAGE_SCHEMA = vol.Schema(
    {
        vol.Required("config_entry"): selector.ConfigEntrySelector(
            {
                "integration": DOMAIN,
            }
        ),
        vol.Optional("model"): cv.string,
        vol.Optional(CONF_API_MODE, default=DEFAULT_API_MODE): vol.In(
            [mode["key"] for mode in API_MODE_OPTIONS]
        ),
        vol.Required("prompt"): cv.string,
        vol.Required("images"): vol.All(
            cv.ensure_list,
            vol.Length(min=1, max=MAX_ATTACHMENT_COUNT),
            [{"url": cv.string}],
        ),
        vol.Optional("max_tokens"): cv.positive_int,
        vol.Optional("azure_model"): cv.string,
    }
)

CHANGE_CONFIG_SCHEMA = vol.Schema(
    {
        vol.Required("config_entry"): selector.ConfigEntrySelector(
            {
                "integration": DOMAIN,
            }
        ),
        vol.Optional(CONF_API_KEY): cv.string,
        vol.Optional(CONF_BASE_URL): cv.string,
        vol.Optional(CONF_API_VERSION): cv.string,
        vol.Optional(CONF_ORGANIZATION): cv.string,
        vol.Optional(CONF_SKIP_AUTHENTICATION): cv.boolean,
        vol.Optional(CONF_API_PROVIDER): vol.In(
            [provider["key"] for provider in API_PROVIDERS]
        ),
    }
)

RELOAD_SKILLS_SCHEMA = vol.Schema({})
CALL_FUNCTION_SCHEMA = vol.Schema(
    {
        vol.Required("function"): cv.string,
        vol.Optional("arguments", default=dict): dict,
    }
)

MEMORY_AGENT_FIELDS: dict[Any, Any] = {
    vol.Required("config_entry"): selector.ConfigEntrySelector({"integration": DOMAIN}),
    vol.Required("agent_id"): cv.string,
}

MEMORY_LIST_SCHEMA = vol.Schema(
    {
        **MEMORY_AGENT_FIELDS,
        vol.Optional("query"): cv.string,
        vol.Optional("category"): cv.string,
        vol.Optional("limit", default=50): vol.All(cv.positive_int, vol.Range(max=100)),
        vol.Optional("offset", default=0): vol.All(vol.Coerce(int), vol.Range(min=0)),
    }
)

MEMORY_DELETE_SCHEMA = vol.Schema(
    {
        **MEMORY_AGENT_FIELDS,
        vol.Required("memory_ids"): vol.All(
            cv.ensure_list, vol.Length(min=1, max=50), [cv.string]
        ),
    }
)

MEMORY_CLEAR_SCHEMA = vol.Schema(
    {
        **MEMORY_AGENT_FIELDS,
        vol.Optional("category"): cv.string,
        vol.Required("confirm", default=False): cv.boolean,
    }
)

FUNCTION_TOOL_STATE_SCHEMA = vol.Schema(
    {
        **MEMORY_AGENT_FIELDS,
        vol.Required("functions"): vol.All(
            cv.ensure_list, vol.Length(min=1, max=100), [cv.string]
        ),
    }
)

FUNCTION_GROUP_STATE_SCHEMA = vol.Schema(
    {
        **MEMORY_AGENT_FIELDS,
        vol.Required("function_groups"): vol.All(
            cv.ensure_list, vol.Length(min=1, max=50), [cv.string]
        ),
    }
)

GUEST_MODE_UPDATE_SCHEMA = vol.Schema(
    {
        **MEMORY_AGENT_FIELDS,
        vol.Optional("active_from"): cv.string,
        vol.Optional("active_until"): cv.string,
        vol.Optional("indefinite", default=False): cv.boolean,
    }
)

GUEST_MODE_DISABLE_SCHEMA = vol.Schema({**MEMORY_AGENT_FIELDS})

PROCESS_SCHEMA = vol.Schema(
    {
        vol.Required("text"): cv.string,
        vol.Optional("agent_id"): selector.EntitySelector(
            {
                "filter": {"integration": DOMAIN, "domain": "conversation"},
            }
        ),
        vol.Optional("conversation_id"): cv.string,
        vol.Optional("device_id"): cv.string,
        vol.Optional("satellite_id"): cv.string,
        vol.Optional("language"): cv.string,
    }
)

_LOGGER = logging.getLogger(__package__)


def resolve_memory_agent(
    hass: HomeAssistant, entry_id: str, agent_reference: str
) -> tuple[str, str]:
    """Resolve a readable conversation entity or legacy subentry ID."""
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        raise HomeAssistantError("Config entry not found")

    registry_entry = er.async_get(hass).async_get(agent_reference)
    if registry_entry is not None:
        if registry_entry.config_entry_id != entry_id:
            raise HomeAssistantError(
                "Conversation agent does not belong to the selected config entry"
            )
        subentry_id = registry_entry.config_subentry_id
        if subentry_id is None:
            raise HomeAssistantError("Conversation agent is not linked to a subentry")
    else:
        # Preserve compatibility with existing actions that pass the raw subentry ID.
        subentry_id = agent_reference

    subentry = entry.subentries.get(subentry_id)
    if subentry is None or subentry.subentry_type != "conversation":
        raise HomeAssistantError("Conversation agent not found")
    return entry_id, subentry_id


async def async_set_function_tools_enabled(
    hass: HomeAssistant,
    entry_id: str,
    agent_reference: str,
    function_names: list[str],
    enabled: bool,
    *,
    user_id: str | None = None,
) -> None:
    """Persist the authoritative enabled state for selected Function Tools."""
    _, subentry_id = resolve_memory_agent(hass, entry_id, agent_reference)
    async with get_agent_maintenance_gate(hass, entry_id, subentry_id).shared():
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None:
            raise HomeAssistantError("Config entry not found")
        subentry = entry.subentries[subentry_id]
        from .helpers import entry_api_provider

        persisted = function_mutations.prepare_function_tools_enabled(
            subentry.data, function_names, enabled, provider=entry_api_provider(entry)
        )
        from .ha_permissions import get_active_ha_context

        context = get_active_ha_context()
        snapshot = await async_capacity_ha_snapshot(
            hass,
            persisted,
            user_id if user_id is not None or context is None else context.user_id,
        )
        validate_runtime_readiness(
            persisted,
            entry.data,
            ha_snapshot=snapshot,
            client=getattr(entry, "runtime_data", None),
        )
        update_live_subentry(hass, entry, subentry, data=persisted)


async def async_set_function_groups_enabled(
    hass: HomeAssistant,
    entry_id: str,
    agent_reference: str,
    group_ids: list[str],
    enabled: bool,
    *,
    user_id: str | None = None,
) -> None:
    """Persist Function Group state without changing member Function Tool state."""
    _, subentry_id = resolve_memory_agent(hass, entry_id, agent_reference)
    async with get_agent_maintenance_gate(hass, entry_id, subentry_id).shared():
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None:
            raise HomeAssistantError("Config entry not found")
        subentry = entry.subentries[subentry_id]
        from .helpers import entry_api_provider

        persisted = function_mutations.prepare_function_groups_enabled(
            subentry.data, group_ids, enabled, provider=entry_api_provider(entry)
        )
        from .ha_permissions import get_active_ha_context

        context = get_active_ha_context()
        snapshot = await async_capacity_ha_snapshot(
            hass,
            persisted,
            user_id if user_id is not None or context is None else context.user_id,
        )
        validate_runtime_readiness(
            persisted,
            entry.data,
            ha_snapshot=snapshot,
            client=getattr(entry, "runtime_data", None),
        )
        update_live_subentry(hass, entry, subentry, data=persisted)


async def _async_require_service_admin(hass: HomeAssistant, call: ServiceCall) -> None:
    """Allow system automations and require admin rights for user-originated calls."""
    user_id = getattr(getattr(call, "context", None), "user_id", None)
    if user_id is None:
        return
    user = await hass.auth.async_get_user(user_id)
    if user is None or not user.is_admin:
        raise HomeAssistantError("Administrator permission is required")


async def async_setup_services(hass: HomeAssistant, config: ConfigType) -> None:
    """Set up services for the extended openai conversation component."""

    async def query_image(call: ServiceCall) -> ServiceResponse:
        """Query an image."""
        await _async_require_service_admin(hass, call)
        entry = None
        try:
            if "max_tokens" in call.data and call.data["max_tokens"] in (None, ""):
                raise HomeAssistantError("Output token limit is required")
            entry = hass.config_entries.async_get_entry(call.data["config_entry"])
            if entry is None or entry.domain != DOMAIN:
                raise HomeAssistantError("Config entry not found")
            if getattr(entry, "state", None) is not ConfigEntryState.LOADED:
                raise HomeAssistantError("Config entry is not loaded")
            from .provider_credentials import require_provider_restore_complete

            require_provider_restore_complete(hass, call.data["config_entry"])
            # Setup can fail before assigning a runtime client. Do not parse
            # images or make a provider request for an unavailable connection.
            client = getattr(entry, "runtime_data", None)
            if client is None:
                raise HomeAssistantError("Config entry client is unavailable")
            entry_data = getattr(entry, "data", {})
            model = call.data.get("model")
            if model is None:
                model = (
                    "gpt-6-luna"
                    if is_native_openai_connection(entry_data, client=client)
                    else "gpt-4.1-mini"
                )
            with model_capability_snapshot(
                model,
                request_model_capabilities(
                    {**call.data, CONF_CHAT_MODEL: model}, entry_data
                ),
            ):
                api_mode = get_api_mode(call.data[CONF_API_MODE], model)
            request_settings = query_image_request_settings(
                model,
                api_mode,
                call.data,
                entry_data,
            )
            image_params = await hass.async_add_executor_job(
                prepare_image_params, hass, call.data["images"]
            )

            if api_mode == API_MODE_RESPONSES:
                messages = [
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": call.data["prompt"]},
                            *[
                                {
                                    "type": "input_image",
                                    "image_url": image["url"],
                                    "detail": "auto",
                                }
                                for image in image_params
                            ],
                        ],
                    }
                ]
                _LOGGER.debug(
                    "Querying %s using %s with %d image(s)",
                    model,
                    api_mode,
                    len(image_params),
                )
                response = await client.responses.create(
                    model=model,
                    input=messages,
                    **request_settings,
                    store=False,
                )
                _ensure_complete_image_result(response, api_mode)
                ensure_successful_responses_result(response)
            else:
                images = [
                    {"type": "image_url", "image_url": image} for image in image_params
                ]
                messages = [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": call.data["prompt"]},
                            *images,
                        ],
                    }
                ]
                _LOGGER.debug(
                    "Querying %s using %s with %d image(s)",
                    model,
                    api_mode,
                    len(image_params),
                )
                response = await client.chat.completions.create(
                    model=model,
                    messages=messages,
                    **request_settings,
                )
                _ensure_complete_image_result(response, api_mode)
            response_dict: dict = response.model_dump()
            _LOGGER.debug("Image query completed using %s", model)
        except OpenAIError as err:
            log_provider_failure(_LOGGER, "Image query provider request failed", err)
            if entry is not None:
                request_reauthentication(hass, entry, err)
            raise HomeAssistantError(
                f"Error generating image: {provider_user_message(err)}"
            ) from err

        return response_dict

    async def change_config(call: ServiceCall) -> None:
        """Change configuration."""
        await _async_require_service_admin(hass, call)
        entry_id = call.data["config_entry"]
        entry = hass.config_entries.async_get_entry(entry_id)
        if not entry or entry.domain != DOMAIN:
            raise HomeAssistantError(f"Config entry {entry_id} not found")

        original_data = dict(getattr(entry, "data", {}))
        updates = {}
        for key in (
            CONF_API_KEY,
            CONF_BASE_URL,
            CONF_API_VERSION,
            CONF_ORGANIZATION,
            CONF_SKIP_AUTHENTICATION,
            CONF_API_PROVIDER,
        ):
            if key in call.data:
                updates[key] = call.data[key]

        if not updates:
            return

        new_data = dict(original_data)
        new_data.update(updates)
        from .provider_credentials import (
            async_validate_agent_provider_request,
            incompatible_provider_agent,
            request_provider_reload_after_update,
            require_provider_restore_complete,
            validate_provider_connection,
        )

        require_provider_restore_complete(hass, entry_id)
        provider = validate_provider_connection(new_data)

        _LOGGER.debug(
            "Updating config entry %s fields: %s",
            entry_id,
            ", ".join(sorted(updates)),
        )

        if provider == "azure":
            new_data[CONF_API_PROVIDER] = provider

        try:
            await get_authenticated_client(
                hass=hass,
                api_key=new_data[CONF_API_KEY],
                base_url=new_data.get(CONF_BASE_URL),
                api_version=new_data.get(CONF_API_VERSION),
                organization=new_data.get(CONF_ORGANIZATION),
                skip_authentication=new_data.get(CONF_SKIP_AUTHENTICATION, False),
                api_provider=new_data.get(CONF_API_PROVIDER),
            )
        except OpenAIError as err:
            log_provider_failure(
                _LOGGER, "Provider configuration validation failed", err
            )
            raise HomeAssistantError(
                f"Provider configuration validation failed: {provider_user_message(err)}"
            ) from err

        from .request_rules import async_get_request_rules

        children = dict(getattr(entry, "subentries", {}))
        child_data = {key: dict(child.data) for key, child in children.items()}
        rule_revisions = []
        endpoint_changed = any(
            original_data.get(key) != new_data.get(key)
            for key in (CONF_API_PROVIDER, CONF_BASE_URL, CONF_API_VERSION)
        )
        for child_id, child in children.items():
            if child.subentry_type not in {"conversation", "ai_task_data"}:
                continue
            options = child_data[child_id]
            try:
                saved_rules = []
                if child.subentry_type == "conversation":
                    rules = await async_get_request_rules(hass, entry_id, child_id)
                    rule_revisions.append((rules, rules.revision()))
                    saved_rules = rules.snapshot()["rules"]
                await async_validate_agent_provider_request(
                    hass,
                    child.subentry_type,
                    options,
                    new_data,
                    rules=saved_rules,
                    user_id=call.context.user_id,
                    discover_ha_tools=endpoint_changed,
                    previous_provider_data=original_data if endpoint_changed else None,
                )
            except HomeAssistantError as err:
                raise incompatible_provider_agent(child.title, err) from err
        if dict(getattr(entry, "subentries", {})) != children or any(
            dict(child.data) != child_data[key] for key, child in children.items()
        ):
            raise HomeAssistantError(
                "Agent configuration changed during provider validation; retry with the latest settings"
            )

        if any(rules.revision() != revision for rules, revision in rule_revisions):
            raise HomeAssistantError(
                "Request Rules changed during provider validation; retry with the latest settings"
            )

        if dict(getattr(entry, "data", {})) != original_data:
            raise HomeAssistantError(
                "Provider settings changed while this configuration was being "
                "validated. No changes were saved by this operation. Try again."
            )

        state_before = entry.state
        changed = hass.config_entries.async_update_entry(entry, data=new_data)
        request_provider_reload_after_update(
            hass,
            entry,
            state_before,
            changed=changed,
            retry_unchanged=not new_data.get(CONF_SKIP_AUTHENTICATION, False),
        )

    async def reload_skills(call: ServiceCall) -> ServiceResponse:
        """Reload skills from the user skill directory."""
        await _async_require_service_admin(hass, call)
        from .skills import SkillManager

        skill_manager = await SkillManager.async_get_instance(hass)
        await skill_manager.async_load_skills()

        return {
            "loaded_skills": len(skill_manager.get_all_skills()),
        }

    async def memory_list(call: ServiceCall) -> ServiceResponse:
        """Inspect memories in the caller's own user scope."""
        entry_id, subentry_id = resolve_memory_agent(
            hass, call.data["config_entry"], call.data["agent_id"]
        )
        async with get_agent_maintenance_gate(hass, entry_id, subentry_id).shared():
            memory = await async_get_memory(hass, entry_id, subentry_id)
            user_id = memory_user_id(call)
            if query := call.data.get("query"):
                records = await memory.async_search(
                    user_id,
                    query,
                    call.data.get("category"),
                    call.data["limit"],
                    offset=call.data["offset"],
                )
            else:
                records = await memory.async_list(
                    user_id,
                    call.data.get("category"),
                    call.data["limit"],
                    call.data["offset"],
                )
            return cast(
                ServiceResponse,
                {"memories": [memory_as_dict(record) for record in records]},
            )

    async def memory_delete(call: ServiceCall) -> ServiceResponse:
        """Delete selected memories in the caller's own user scope."""
        entry_id, subentry_id = resolve_memory_agent(
            hass, call.data["config_entry"], call.data["agent_id"]
        )
        async with get_agent_maintenance_gate(hass, entry_id, subentry_id).shared():
            memory = await async_get_memory(hass, entry_id, subentry_id)
            deleted = await memory.async_delete(
                memory_user_id(call), call.data["memory_ids"]
            )
            return {"deleted": deleted}

    async def memory_clear(call: ServiceCall) -> ServiceResponse:
        """Clear memories only after explicit confirmation."""
        if call.data["confirm"] is not True:
            raise HomeAssistantError("Set confirm to true to clear memories")
        entry_id, subentry_id = resolve_memory_agent(
            hass, call.data["config_entry"], call.data["agent_id"]
        )
        async with get_agent_maintenance_gate(hass, entry_id, subentry_id).shared():
            memory = await async_get_memory(hass, entry_id, subentry_id)
            deleted = await memory.async_clear(
                memory_user_id(call), call.data.get("category")
            )
            return {"deleted": deleted}

    async def enable_function_tools(call: ServiceCall) -> None:
        """Enable one or more configured Function Tools."""
        await _async_require_service_admin(hass, call)
        await async_set_function_tools_enabled(
            hass,
            call.data["config_entry"],
            call.data["agent_id"],
            call.data["functions"],
            True,
            user_id=call.context.user_id,
        )

    async def rebuild_memory_embeddings(call: ServiceCall) -> None:
        """Reset derived embeddings for an agent while preserving memories."""
        await _async_require_service_admin(hass, call)
        entry_id, subentry_id = resolve_memory_agent(
            hass, call.data["config_entry"], call.data["agent_id"]
        )
        async with get_agent_maintenance_gate(hass, entry_id, subentry_id).exclusive():
            memory = await async_get_memory(hass, entry_id, subentry_id)
            await memory.async_rebuild_embedding_cache()

    async def disable_function_tools(call: ServiceCall) -> None:
        """Disable one or more configured Function Tools."""
        await _async_require_service_admin(hass, call)
        await async_set_function_tools_enabled(
            hass,
            call.data["config_entry"],
            call.data["agent_id"],
            call.data["functions"],
            False,
            user_id=call.context.user_id,
        )

    async def enable_function_groups(call: ServiceCall) -> None:
        """Enable one or more configured Function Groups."""
        await _async_require_service_admin(hass, call)
        await async_set_function_groups_enabled(
            hass,
            call.data["config_entry"],
            call.data["agent_id"],
            call.data["function_groups"],
            True,
            user_id=call.context.user_id,
        )

    async def disable_function_groups(call: ServiceCall) -> None:
        """Disable one or more configured Function Groups."""
        await _async_require_service_admin(hass, call)
        await async_set_function_groups_enabled(
            hass,
            call.data["config_entry"],
            call.data["agent_id"],
            call.data["function_groups"],
            False,
            user_id=call.context.user_id,
        )

    async def guest_mode_update(call: ServiceCall) -> ServiceResponse:
        """Set the full Guest Mode interval from a trusted HA action."""
        await _async_require_service_admin(hass, call)
        entry_id, subentry_id = resolve_memory_agent(
            hass, call.data["config_entry"], call.data["agent_id"]
        )
        async with get_agent_maintenance_gate(hass, entry_id, subentry_id).shared():
            manager = await async_get_guest_mode(hass, entry_id, subentry_id)
            try:
                return cast(
                    ServiceResponse,
                    await manager.async_update_trusted(
                        active_from=call.data.get("active_from"),
                        active_until=call.data.get("active_until"),
                        indefinite=call.data["indefinite"],
                    ),
                )
            except ValueError as err:
                raise HomeAssistantError(str(err)) from err

    def _process_agent(agent_id: str | None):
        if agent_id:
            agent = conversation.async_get_agent(hass, agent_id)
            if agent is None or not hasattr(agent, "async_process_direct"):
                raise HomeAssistantError("Extended OpenAI conversation agent not found")
            return agent
        registry = er.async_get(hass)
        agents = []
        for registry_entry in registry.entities.values():
            if (
                registry_entry.platform == DOMAIN
                and registry_entry.domain == "conversation"
            ):
                agent = conversation.async_get_agent(hass, registry_entry.entity_id)
                if agent is not None and hasattr(agent, "async_process_direct"):
                    agents.append(agent)
        if not agents:
            raise HomeAssistantError(
                "No Extended OpenAI conversation agent is available"
            )
        if len(agents) > 1:
            raise HomeAssistantError(
                "Choose a conversation agent when more than one is configured"
            )
        return agents[0]

    async def process(call: ServiceCall) -> ServiceResponse:
        """Send text directly through an Extended OpenAI entity's normal pipeline."""
        if not call.data["text"].strip():
            raise HomeAssistantError("Request text cannot be empty")
        agent = _process_agent(call.data.get("agent_id"))
        agent_id = getattr(agent, "entity_id", call.data.get("agent_id") or "")
        user_input = ConversationInput(
            text=call.data["text"],
            context=call.context,
            conversation_id=call.data.get("conversation_id"),
            device_id=call.data.get("device_id"),
            satellite_id=call.data.get("satellite_id"),
            language=call.data.get("language", hass.config.language),
            agent_id=agent_id,
        )
        try:
            result, metadata = await agent.async_process_direct(user_input)
        except Exception as err:
            if isinstance(err, HomeAssistantError):
                raise
            raise HomeAssistantError(
                f"Extended OpenAI could not process the request: {err}"
            ) from err
        speech = getattr(result.response, "speech", {}) or {}
        response_text = ""
        if isinstance(speech, Mapping):
            plain = speech.get("plain", {})
            if isinstance(plain, Mapping):
                response_text = str(plain.get("speech", ""))
        error_code = getattr(result.response, "error_code", None)
        structured = (
            result.response.as_dict()
            if callable(getattr(result.response, "as_dict", None))
            else {}
        )
        response: dict[str, Any] = {
            "successful": error_code is None,
            "error_code": getattr(error_code, "value", error_code),
            "error_message": response_text if error_code is not None else None,
            "continue_conversation": bool(
                getattr(result, "continue_conversation", False)
            ),
            "intent_response": structured,
            "response": response_text,
            "conversation_id": result.conversation_id,
            "handled_locally": bool(metadata.get("handled_locally")),
        }
        if metadata.get("matched_rule") is not None:
            response["matched_rule"] = metadata["matched_rule"]
            response["captured_values"] = metadata.get("captured_values", {})
        return response

    async def guest_mode_disable(call: ServiceCall) -> ServiceResponse:
        """End or cancel Guest Mode from a trusted HA action."""
        await _async_require_service_admin(hass, call)
        entry_id, subentry_id = resolve_memory_agent(
            hass, call.data["config_entry"], call.data["agent_id"]
        )
        async with get_agent_maintenance_gate(hass, entry_id, subentry_id).shared():
            manager = await async_get_guest_mode(hass, entry_id, subentry_id)
            return cast(ServiceResponse, await manager.async_disable_trusted())

    async def call_function(call: ServiceCall) -> ServiceResponse:
        """Bridge a native HA script action into the active configured function."""
        result = await async_call_active_function(
            call.data["function"], call.data.get("arguments", {})
        )
        from .ha_tool_result_compat import ScheduledExecution

        return cast(
            ServiceResponse,
            dict(result)
            if isinstance(result, ScheduledExecution)
            else {"result": result},
        )

    hass.services.async_register(
        DOMAIN,
        SERVICE_PROCESS,
        process,
        schema=schema_for_ha(PROCESS_SCHEMA),
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_CALL_FUNCTION,
        call_function,
        schema=schema_for_ha(CALL_FUNCTION_SCHEMA),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_QUERY_IMAGE,
        query_image,
        schema=schema_for_ha(QUERY_IMAGE_SCHEMA),
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        "change_config",
        change_config,
        schema=schema_for_ha(CHANGE_CONFIG_SCHEMA),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_RELOAD_SKILLS,
        reload_skills,
        schema=schema_for_ha(RELOAD_SKILLS_SCHEMA),
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_MEMORY_LIST,
        memory_list,
        schema=schema_for_ha(MEMORY_LIST_SCHEMA),
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_MEMORY_DELETE,
        memory_delete,
        schema=schema_for_ha(MEMORY_DELETE_SCHEMA),
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_MEMORY_CLEAR,
        memory_clear,
        schema=schema_for_ha(MEMORY_CLEAR_SCHEMA),
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_ENABLE_FUNCTION_TOOLS,
        enable_function_tools,
        schema=schema_for_ha(FUNCTION_TOOL_STATE_SCHEMA),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_REBUILD_MEMORY_EMBEDDINGS,
        rebuild_memory_embeddings,
        schema=schema_for_ha(vol.Schema(MEMORY_AGENT_FIELDS)),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_DISABLE_FUNCTION_TOOLS,
        disable_function_tools,
        schema=schema_for_ha(FUNCTION_TOOL_STATE_SCHEMA),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_ENABLE_FUNCTION_GROUPS,
        enable_function_groups,
        schema=schema_for_ha(FUNCTION_GROUP_STATE_SCHEMA),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_DISABLE_FUNCTION_GROUPS,
        disable_function_groups,
        schema=schema_for_ha(FUNCTION_GROUP_STATE_SCHEMA),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_GUEST_MODE_UPDATE,
        guest_mode_update,
        schema=schema_for_ha(GUEST_MODE_UPDATE_SCHEMA),
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_GUEST_MODE_DISABLE,
        guest_mode_disable,
        schema=schema_for_ha(GUEST_MODE_DISABLE_SCHEMA),
        supports_response=SupportsResponse.ONLY,
    )


def _ensure_complete_image_result(response: Any, api_mode: str) -> None:
    """Reject incomplete auxiliary answers without a retry or reauthentication."""

    def field(value: Any, key: str) -> Any:
        return (
            value.get(key) if isinstance(value, Mapping) else getattr(value, key, None)
        )

    exhausted = False
    if api_mode == API_MODE_RESPONSES:
        if field(response, "status") != "incomplete":
            return
        exhausted = (
            field(field(response, "incomplete_details"), "reason")
            == "max_output_tokens"
        )
    else:
        finish_reasons = tuple(
            field(choice, "finish_reason")
            for choice in field(response, "choices") or []
        )
        if "content_filter" in finish_reasons:
            raise HomeAssistantError(
                "Image query was blocked by the provider content filter"
            )
        exhausted = "length" in finish_reasons
        if not exhausted:
            return
    if exhausted:
        raise HomeAssistantError(
            "Image query exhausted its output token limit before completing. "
            "Increase max_tokens to allow room for the answer and any reasoning tokens."
        )
    raise HomeAssistantError("Image query returned an incomplete response")


def _convert_image_param(
    hass: HomeAssistant, image: dict, total_bytes: int
) -> tuple[dict, int]:
    """Convert one local image to a bounded data URL, or preserve a remote URL."""
    result = dict(image)
    url = result["url"]

    if urlparse(url).scheme in cv.EXTERNAL_URL_PROTOCOL_SCHEMA_LIST:
        return result, 0

    if not hass.config.is_allowed_path(url):
        raise HomeAssistantError(
            f"Cannot read `{url}`, no access to path; "
            "`allowlist_external_dirs` may need to be adjusted in "
            "`configuration.yaml`"
        )

    path = Path(url)
    if not path.exists():
        raise HomeAssistantError(f"`{url}` does not exist")
    if not path.is_file():
        raise HomeAssistantError(f"`{url}` is not a file")

    mime_type, _ = mimetypes.guess_type(url)
    if mime_type is None or not mime_type.startswith("image"):
        raise HomeAssistantError(f"`{url}` is not an image")

    content = read_bounded_local_file(path, total_bytes)
    result["url"] = (
        f"data:{mime_type};base64,{base64.b64encode(content).decode('utf-8')}"
    )
    return result, len(content)


def prepare_image_params(hass: HomeAssistant, images: list[dict]) -> list[dict]:
    """Prepare a bounded set of image parameters outside the event loop."""
    if len(images) > MAX_ATTACHMENT_COUNT:
        raise HomeAssistantError(
            f"At most {MAX_ATTACHMENT_COUNT} images can be sent in one request"
        )

    prepared: list[dict] = []
    total_bytes = 0
    for image in images:
        converted, size = _convert_image_param(hass, image, total_bytes)
        total_bytes += size
        prepared.append(converted)
    return prepared


def to_image_param(hass: HomeAssistant, image: dict) -> dict:
    """Convert a single URL to a bounded base64 encoded image if local."""
    return _convert_image_param(hass, image, 0)[0]


def encode_image(image_path: str) -> str:
    """Convert a bounded local file to base64 encoded image."""
    content = read_bounded_local_file(Path(image_path))
    return base64.b64encode(content).decode("utf-8")
