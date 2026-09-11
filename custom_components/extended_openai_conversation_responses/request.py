"""Pure production request assembly helpers shared with management previews."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
import logging
from typing import Any

from homeassistant.exceptions import HomeAssistantError

from .capabilities import resolve_effective_capabilities
from .const import (
    API_MODE_RESPONSES,
    CONF_API_MODE,
    CONF_API_PROVIDER,
    CONF_ARCHIVE_ENABLED,
    CONF_ARCHIVE_MODEL_SEARCH_ENABLED,
    CONF_BASE_URL,
    CONF_CHAT_MODEL,
    CONF_FUNCTION_GROUPS,
    CONF_FUNCTION_TOOLS,
    CONF_GUEST_MODE_ENABLED,
    CONF_KNOWLEDGE_ENABLED,
    CONF_MAX_TOKENS,
    CONF_MEMORY_ENABLED,
    CONF_REASONING_EFFORT,
    CONF_SERVICE_TIER,
    CONF_TEMPERATURE,
    CONF_TOP_P,
    CONF_WEB_SEARCH,
    CONF_WEB_SEARCH_CONTEXT,
    DEFAULT_API_MODE,
    DEFAULT_ARCHIVE_ENABLED,
    DEFAULT_ARCHIVE_MODEL_SEARCH_ENABLED,
    DEFAULT_CHAT_MODEL,
    DEFAULT_MAX_TOKENS,
    DEFAULT_REASONING_EFFORT,
    DEFAULT_SERVICE_TIER,
    DEFAULT_TEMPERATURE,
    DEFAULT_TOP_P,
    DEFAULT_WEB_SEARCH,
    DEFAULT_WEB_SEARCH_CONTEXT,
)
from .conversation_archive import archive_tools
from .conversation_lifecycle import conversation_lifecycle_active
from .guest_mode import GuestCapabilityPolicy, guest_mode_restrict_tool
from .helpers import supports_openai_hosted_tools
from .knowledge import KNOWLEDGE_TOOL_NAMES, knowledge_tools
from .memory import MEMORY_TOOL_NAMES, memory_tools
from .model_capabilities import (
    ModelCapabilityError,
    get_model_capabilities,
    normalize_output_token_limit,
    parameter_is_allowed,
    recommended_reasoning_effort,
    sampling_value_is_configured,
    select_api_path,
    validate_reasoning_effort,
)
from .model_payload import prepare_model_function_tools
from .temporary_memory import TEMPORARY_MEMORY_TOOL_NAMES, temporary_memory_tools

_LOGGER = logging.getLogger(__name__)
_LEGACY_TOKEN_MIGRATION_LOGGED: set[str] = set()

CONTINUE_CONVERSATION_TOOL_NAME = "set_continue_conversation"
CONTINUE_CONVERSATION_TOOL = {
    "spec": {
        "name": CONTINUE_CONVERSATION_TOOL_NAME,
        "description": (
            "Return the final spoken response and whether the user is expected to "
            "reply immediately. Use this only when the final answer is ready."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "response": {
                    "type": "string",
                    "description": "The complete plain-text response spoken to the user.",
                },
                "continue_conversation": {
                    "type": "boolean",
                    "description": "Whether to listen for an immediate follow-up.",
                },
            },
            "required": ["response", "continue_conversation"],
            "additionalProperties": False,
        },
    }
}

START_FRESH_CONVERSATION_TOOL_NAME = "start_fresh_conversation"
START_FRESH_CONVERSATION_TOOL = {
    "spec": {
        "name": START_FRESH_CONVERSATION_TOOL_NAME,
        "description": (
            "Start a fresh Assist conversation after this response when the user "
            "explicitly asks to start over or reset the current discussion. This "
            "clears only current conversation context and conversation-scoped "
            "routing/tool state. It does not delete persistent memory, temporary "
            "memory, Knowledge Library sources, or archived history."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    },
    "function": {
        "type": "conversation_lifecycle",
        "operation": "start_fresh",
    },
}


@dataclass(frozen=True, slots=True)
class ProviderRequestSnapshot:
    """Provider-controlled request pieces assembled before a live call."""

    api_mode: str
    api_kwargs: dict[str, Any]
    provider_tools: tuple[dict[str, Any], ...]


def canonical_json(value: Any) -> str:
    """Serialize structured preview content deterministically and compactly."""
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def format_function_tools(
    function_tools: list[dict[str, Any]], api_mode: str
) -> list[dict[str, Any]]:
    """Format compact model-facing definitions exactly as the API expects."""
    model_tools = prepare_model_function_tools(function_tools)
    if api_mode == API_MODE_RESPONSES:
        return [{"type": "function", **tool["spec"]} for tool in model_tools]
    return [{"type": "function", "function": tool["spec"]} for tool in model_tools]


def build_web_search_tool(
    options: Mapping[str, Any],
    api_mode: str,
    entry_data: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Build the native OpenAI Responses Web Search tool when enabled."""
    if not options.get(CONF_WEB_SEARCH, DEFAULT_WEB_SEARCH):
        return None
    if api_mode != API_MODE_RESPONSES:
        raise HomeAssistantError(
            "Web Search requires the Responses API. Select Responses API mode or "
            "use a model for which Auto resolves to Responses."
        )
    if not supports_openai_hosted_tools(
        entry_data.get(CONF_API_PROVIDER), entry_data.get(CONF_BASE_URL)
    ):
        raise HomeAssistantError(
            "Web Search is available only with the direct OpenAI Responses API; "
            "Azure and custom base URLs are not supported."
        )
    return {
        "type": "web_search",
        "search_context_size": options.get(
            CONF_WEB_SEARCH_CONTEXT, DEFAULT_WEB_SEARCH_CONTEXT
        ),
    }


def _configured_tools_required(options: Mapping[str, Any]) -> bool:
    """Conservatively identify requests that can expose integration/provider tools."""
    configured = options.get(CONF_FUNCTION_TOOLS)
    if isinstance(configured, str):
        if configured.strip() not in {"", "[]", "null", "~"}:
            return True
    elif configured:
        return True
    if options.get(CONF_FUNCTION_GROUPS):
        return True
    return any(
        bool(options.get(key))
        for key in (
            CONF_MEMORY_ENABLED,
            CONF_KNOWLEDGE_ENABLED,
            CONF_ARCHIVE_ENABLED,
            CONF_GUEST_MODE_ENABLED,
            CONF_WEB_SEARCH,
        )
    )


def _sampling_value(
    options: Mapping[str, Any],
    model: str,
    parameter: str,
    effort: str | None,
) -> Any | None:
    """Return a valid explicitly configured sampling value, else omit it."""
    if parameter == CONF_TEMPERATURE:
        legacy_default = DEFAULT_TEMPERATURE
    else:
        legacy_default = DEFAULT_TOP_P
    value = options.get(parameter, legacy_default)
    if not sampling_value_is_configured(parameter, value, legacy_default):
        return None
    if parameter_is_allowed(model, parameter, effort):
        return value
    _LOGGER.debug(
        "Omitting stale %s=%r for model %s at reasoning_effort=%r because the "
        "active capability catalogue marks it invalid or undocumented",
        parameter,
        value,
        model,
        effort,
    )
    return None


def build_provider_request_snapshot(
    options: Mapping[str, Any],
    entry_data: Mapping[str, Any],
    *,
    tools_required: bool | None = None,
) -> ProviderRequestSnapshot:
    """Build validated/normalized settings used by the live OpenAI request."""
    model = str(options.get(CONF_CHAT_MODEL, DEFAULT_CHAT_MODEL))
    needs_tools = _configured_tools_required(options) if tools_required is None else tools_required
    configured_api = str(options.get(CONF_API_MODE, DEFAULT_API_MODE))
    try:
        api_mode = select_api_path(model, configured_api, needs_tools)
        capabilities = get_model_capabilities(model)
        api_kwargs: dict[str, Any] = {"model": model, "stream": True}

        max_tokens = options.get(CONF_MAX_TOKENS, DEFAULT_MAX_TOKENS)
        normalized_limit = normalize_output_token_limit(model, api_mode, max_tokens)
        if normalized_limit is not None:
            field, value = normalized_limit
            api_kwargs[field] = value
            if CONF_MAX_TOKENS in options and model not in _LEGACY_TOKEN_MIGRATION_LOGGED:
                _LOGGER.debug(
                    "Normalizing persisted max_tokens for %s to API-specific %s; "
                    "deprecated max_tokens will not be sent",
                    model,
                    field,
                )
                _LEGACY_TOKEN_MIGRATION_LOGGED.add(model)

        if api_mode == API_MODE_RESPONSES:
            api_kwargs["store"] = False
        else:
            api_kwargs["stream_options"] = {"include_usage": True}

        effort: str | None = None
        if capabilities["reasoning"]["supported"]:
            raw_effort = options.get(CONF_REASONING_EFFORT)
            if raw_effort is None:
                raw_effort = recommended_reasoning_effort(model)
            effort = validate_reasoning_effort(
                model, str(raw_effort) if raw_effort is not None else None
            )
            if effort is not None:
                if api_mode == API_MODE_RESPONSES:
                    api_kwargs["reasoning"] = {"effort": effort}
                    api_kwargs["include"] = ["reasoning.encrypted_content"]
                else:
                    api_kwargs["reasoning_effort"] = effort
        else:
            stale_effort = options.get(CONF_REASONING_EFFORT)
            if stale_effort not in {None, DEFAULT_REASONING_EFFORT}:
                _LOGGER.debug(
                    "Ignoring stale reasoning_effort=%r for non-reasoning model %s",
                    stale_effort,
                    model,
                )

        temperature = _sampling_value(options, model, CONF_TEMPERATURE, effort)
        if temperature is not None:
            api_kwargs[CONF_TEMPERATURE] = temperature
        top_p = _sampling_value(options, model, CONF_TOP_P, effort)
        if top_p is not None:
            api_kwargs[CONF_TOP_P] = top_p

        if capabilities.get("service_tier"):
            api_kwargs["service_tier"] = options.get(
                CONF_SERVICE_TIER, DEFAULT_SERVICE_TIER
            )
    except ModelCapabilityError as err:
        raise HomeAssistantError(str(err)) from err

    provider_tool = build_web_search_tool(options, api_mode, entry_data)
    return ProviderRequestSnapshot(
        api_mode,
        api_kwargs,
        (provider_tool,) if provider_tool is not None else (),
    )


def assemble_integration_function_tools(
    options: Mapping[str, Any],
    configured_names: set[str],
    *,
    memory_scope_available: bool,
    temporary_scope_available: bool,
    knowledge_available: bool,
    archive_available: bool = True,
    guest_policy: GuestCapabilityPolicy | None = None,
) -> list[dict[str, Any]]:
    """Assemble non-configured tools from the same feature/scope decisions."""
    result: list[dict[str, Any]] = []
    guest_policy = guest_policy or GuestCapabilityPolicy.unrestricted()
    capabilities = resolve_effective_capabilities(
        options,
        memory_scope_available=memory_scope_available,
        guest_policy=guest_policy,
    )
    if START_FRESH_CONVERSATION_TOOL_NAME in configured_names:
        raise HomeAssistantError(
            "Reserved conversation lifecycle tool name configured: "
            f"{START_FRESH_CONVERSATION_TOOL_NAME}"
        )
    if conversation_lifecycle_active():
        result.append(START_FRESH_CONVERSATION_TOOL)
    if capabilities.persistent_memory:
        conflicts = configured_names & MEMORY_TOOL_NAMES
        if conflicts:
            raise HomeAssistantError(
                "Reserved persistent-memory tool name configured: "
                + ", ".join(sorted(conflicts))
            )
        configured_memory_tools = memory_tools()
        if guest_policy.guest_active:
            readable = guest_policy.shared_memory_read
            writable = guest_policy.shared_memory_write
            configured_memory_tools = [
                tool
                for tool in configured_memory_tools
                if (
                    tool["spec"]["name"] in {"memory_search", "memory_list"}
                    and readable
                )
                or (
                    tool["spec"]["name"]
                    in {
                        "memory_add",
                        "memory_upsert",
                        "memory_update",
                        "memory_delete",
                    }
                    and writable
                )
            ]
        result.extend(configured_memory_tools)
    if temporary_scope_available and guest_policy.temporary_memory:
        conflicts = configured_names & TEMPORARY_MEMORY_TOOL_NAMES
        if conflicts:
            raise HomeAssistantError(
                "Reserved temporary-memory tool name configured: "
                + ", ".join(sorted(conflicts))
            )
        result.extend(temporary_memory_tools())
    if (
        archive_available
        and options.get(CONF_ARCHIVE_ENABLED, DEFAULT_ARCHIVE_ENABLED)
        and guest_policy.archive_access
    ):
        configured_archive_tools = archive_tools()
        if not options.get(
            CONF_ARCHIVE_MODEL_SEARCH_ENABLED,
            DEFAULT_ARCHIVE_MODEL_SEARCH_ENABLED,
        ):
            configured_archive_tools = [
                tool
                for tool in configured_archive_tools
                if tool["function"]["operation"] not in {"search", "get"}
            ]
        result.extend(configured_archive_tools)
    if knowledge_available and guest_policy.knowledge_access:
        conflicts = configured_names & KNOWLEDGE_TOOL_NAMES
        if conflicts:
            raise HomeAssistantError(
                "Reserved Knowledge Library tool name configured: "
                + ", ".join(sorted(conflicts))
            )
        result.extend(knowledge_tools())
    if options.get(CONF_GUEST_MODE_ENABLED, False):
        if "guest_mode_restrict" in configured_names:
            raise HomeAssistantError(
                "Reserved Guest Mode tool name configured: guest_mode_restrict"
            )
        result.append(guest_mode_restrict_tool())
    for tool in result:
        if tool.get("spec", {}).get("name") not in {
            "memory_search",
            "conversation_search",
            "knowledge_search",
        }:
            continue
        query_schema = (
            tool.get("spec", {})
            .get("parameters", {})
            .get("properties", {})
            .get("query")
        )
        if isinstance(query_schema, dict):
            query_schema["minLength"] = 1
    return result
