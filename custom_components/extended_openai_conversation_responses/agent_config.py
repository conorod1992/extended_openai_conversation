"""Authoritative conversation-agent configuration contract."""

from __future__ import annotations

from copy import deepcopy
import re
from types import MappingProxyType
from typing import Any, cast

import yaml

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import template

from .const import (
    API_MODE_OPTIONS,
    ARCHIVE_RETENTION_OPTIONS,
    CONF_ADVANCED_OPTIONS,
    CONF_API_MODE,
    CONF_ARCHIVE_ENABLED,
    CONF_ARCHIVE_MODEL_SEARCH_ENABLED,
    CONF_ARCHIVE_RETENTION_DAYS,
    CONF_ARCHIVE_SESSION_TIMEOUT_MINUTES,
    CONF_CHAT_MODEL,
    CONF_CONTEXT_THRESHOLD,
    CONF_CONTEXT_TRUNCATE_STRATEGY,
    CONF_CONTINUE_CONVERSATION,
    CONF_CONVERSATION_CONTINUITY,
    CONF_CONVERSATION_TIMEOUT_MINUTES,
    CONF_CURRENT_DATETIME_ENABLED,
    CONF_CURRENT_DATETIME_TEMPLATE,
    CONF_EXPOSED_ENTITIES_ENABLED,
    CONF_EXPOSED_ENTITIES_TEMPLATE,
    CONF_FUNCTION_GROUPS,
    CONF_FUNCTION_TOOLS,
    CONF_KNOWLEDGE_ENABLED,
    CONF_MAX_FUNCTION_CALLS_PER_CONVERSATION,
    CONF_MAX_TOKENS,
    CONF_MEMORY_AUTO_CREATE,
    CONF_MEMORY_AUTO_RETRIEVE_LIMIT,
    CONF_MEMORY_ENABLED,
    CONF_MEMORY_MODE,
    CONF_PROMPT,
    CONF_REASONING_EFFORT,
    CONF_SERVICE_TIER,
    CONF_SHARED_ARCHIVE_ENABLED,
    CONF_SHARED_MEMORY_MODE,
    CONF_SHORTEN_TOOL_CALL_ID,
    CONF_SKILLS,
    CONF_SPEECH_PROCESSING_ENABLED,
    CONF_SPEECH_REGEX_REPLACEMENTS,
    CONF_SPEECH_STRIP_MARKDOWN,
    CONF_SPEECH_STRIP_URLS,
    CONF_TEMPERATURE,
    CONF_TEMPORARY_MEMORY,
    CONF_TOP_P,
    CONF_USAGE_REQUEST_RETENTION_DAYS,
    CONF_USAGE_RUN_RETENTION_DAYS,
    CONF_VOICE_DEFAULT_USER_ID,
    CONF_VOICE_DEVICE_MAPPINGS,
    CONF_VOICE_SCOPE_POLICY,
    CONF_VOICE_UNMAPPED_POLICY,
    CONF_WEB_SEARCH,
    CONF_WEB_SEARCH_CONTEXT,
    CONTEXT_TRUNCATE_STRATEGIES,
    CONTINUE_CONVERSATION_OPTIONS,
    CONVERSATION_CONTINUITY_OPTIONS,
    CONVERSATION_TIMEOUT_OPTIONS,
    DEFAULT_ADVANCED_OPTIONS,
    DEFAULT_API_MODE,
    DEFAULT_ARCHIVE_ENABLED,
    DEFAULT_ARCHIVE_MODEL_SEARCH_ENABLED,
    DEFAULT_ARCHIVE_RETENTION_DAYS,
    DEFAULT_ARCHIVE_SESSION_TIMEOUT_MINUTES,
    DEFAULT_CHAT_MODEL,
    DEFAULT_CONF_FUNCTION_TOOLS,
    DEFAULT_CONTEXT_THRESHOLD,
    DEFAULT_CONTEXT_TRUNCATE_STRATEGY,
    DEFAULT_CONTINUE_CONVERSATION,
    DEFAULT_CONVERSATION_CONTINUITY,
    DEFAULT_CONVERSATION_TIMEOUT_MINUTES,
    DEFAULT_CURRENT_DATETIME_ENABLED,
    DEFAULT_CURRENT_DATETIME_TEMPLATE,
    DEFAULT_EXPOSED_ENTITIES_ENABLED,
    DEFAULT_EXPOSED_ENTITIES_TEMPLATE,
    DEFAULT_FUNCTION_GROUPS,
    DEFAULT_KNOWLEDGE_ENABLED,
    DEFAULT_MAX_FUNCTION_CALLS_PER_CONVERSATION,
    DEFAULT_MAX_TOKENS,
    DEFAULT_MEMORY_AUTO_RETRIEVE_LIMIT,
    DEFAULT_MEMORY_MODE,
    DEFAULT_PROMPT,
    DEFAULT_REASONING_EFFORT,
    DEFAULT_SERVICE_TIER,
    DEFAULT_SHARED_ARCHIVE_ENABLED,
    DEFAULT_SHARED_MEMORY_MODE,
    DEFAULT_SHORTEN_TOOL_CALL_ID,
    DEFAULT_SPEECH_PROCESSING_ENABLED,
    DEFAULT_SPEECH_REGEX_REPLACEMENTS,
    DEFAULT_SPEECH_STRIP_MARKDOWN,
    DEFAULT_SPEECH_STRIP_URLS,
    DEFAULT_TEMPERATURE,
    DEFAULT_TEMPORARY_MEMORY,
    DEFAULT_TOP_P,
    DEFAULT_USAGE_REQUEST_RETENTION_DAYS,
    DEFAULT_USAGE_RUN_RETENTION_DAYS,
    DEFAULT_VOICE_SCOPE_POLICY,
    DEFAULT_VOICE_UNMAPPED_POLICY,
    DEFAULT_WEB_SEARCH,
    DEFAULT_WEB_SEARCH_CONTEXT,
    FUNCTION_GROUP_LOADER_TOOL_NAME,
    FUNCTION_GROUP_LOADING_MODES,
    FUNCTION_GROUP_LOADING_ON_DEMAND,
    MAX_MEMORY_AUTO_RETRIEVE_LIMIT,
    MAX_SPEECH_REGEX_PATTERN_LENGTH,
    MAX_SPEECH_REGEX_REPLACEMENT_LENGTH,
    MAX_SPEECH_REGEX_RULES,
    MEMORY_MODES,
    REASONING_EFFORT_OPTIONS,
    SERVICE_TIER_OPTIONS,
    SHARED_MEMORY_MODES,
    TEMPORARY_MEMORY_OPTIONS,
    USAGE_RETENTION_OPTIONS,
    VOICE_POLICIES,
    WEB_SEARCH_CONTEXT_OPTIONS,
)
from .functions import FUNCTIONS, get_function
from .helpers import get_model_config
from .memory import get_memory_mode


class AgentConfigError(HomeAssistantError):
    """A validation error tied to one agent configuration field."""

    def __init__(self, field: str, message: str) -> None:
        self.field = field
        super().__init__(f"{field}: {message}")


def _tools_yaml(value: Any) -> str:
    tools = validate_function_tools(value)
    return yaml.safe_dump(tools, sort_keys=False, allow_unicode=True)


AGENT_CONFIG_DEFAULTS = MappingProxyType(
    {
        CONF_PROMPT: DEFAULT_PROMPT,
        CONF_CURRENT_DATETIME_ENABLED: DEFAULT_CURRENT_DATETIME_ENABLED,
        CONF_CURRENT_DATETIME_TEMPLATE: DEFAULT_CURRENT_DATETIME_TEMPLATE,
        CONF_EXPOSED_ENTITIES_ENABLED: DEFAULT_EXPOSED_ENTITIES_ENABLED,
        CONF_EXPOSED_ENTITIES_TEMPLATE: DEFAULT_EXPOSED_ENTITIES_TEMPLATE,
        CONF_CHAT_MODEL: DEFAULT_CHAT_MODEL,
        CONF_API_MODE: DEFAULT_API_MODE,
        CONF_MAX_TOKENS: DEFAULT_MAX_TOKENS,
        CONF_MAX_FUNCTION_CALLS_PER_CONVERSATION: DEFAULT_MAX_FUNCTION_CALLS_PER_CONVERSATION,
        CONF_TOP_P: DEFAULT_TOP_P,
        CONF_TEMPERATURE: DEFAULT_TEMPERATURE,
        CONF_REASONING_EFFORT: DEFAULT_REASONING_EFFORT,
        CONF_SERVICE_TIER: DEFAULT_SERVICE_TIER,
        CONF_FUNCTION_TOOLS: yaml.safe_dump(
            DEFAULT_CONF_FUNCTION_TOOLS, sort_keys=False, allow_unicode=True
        ),
        CONF_FUNCTION_GROUPS: list(DEFAULT_FUNCTION_GROUPS),
        CONF_CONTEXT_THRESHOLD: DEFAULT_CONTEXT_THRESHOLD,
        CONF_CONTEXT_TRUNCATE_STRATEGY: DEFAULT_CONTEXT_TRUNCATE_STRATEGY,
        CONF_CONTINUE_CONVERSATION: DEFAULT_CONTINUE_CONVERSATION,
        CONF_CONVERSATION_CONTINUITY: DEFAULT_CONVERSATION_CONTINUITY,
        CONF_CONVERSATION_TIMEOUT_MINUTES: DEFAULT_CONVERSATION_TIMEOUT_MINUTES,
        CONF_WEB_SEARCH: DEFAULT_WEB_SEARCH,
        CONF_WEB_SEARCH_CONTEXT: DEFAULT_WEB_SEARCH_CONTEXT,
        CONF_MEMORY_MODE: DEFAULT_MEMORY_MODE,
        CONF_TEMPORARY_MEMORY: DEFAULT_TEMPORARY_MEMORY,
        CONF_MEMORY_ENABLED: False,
        CONF_MEMORY_AUTO_CREATE: False,
        CONF_MEMORY_AUTO_RETRIEVE_LIMIT: DEFAULT_MEMORY_AUTO_RETRIEVE_LIMIT,
        CONF_KNOWLEDGE_ENABLED: DEFAULT_KNOWLEDGE_ENABLED,
        CONF_ARCHIVE_ENABLED: DEFAULT_ARCHIVE_ENABLED,
        CONF_ARCHIVE_RETENTION_DAYS: DEFAULT_ARCHIVE_RETENTION_DAYS,
        CONF_ARCHIVE_MODEL_SEARCH_ENABLED: DEFAULT_ARCHIVE_MODEL_SEARCH_ENABLED,
        CONF_SHARED_ARCHIVE_ENABLED: DEFAULT_SHARED_ARCHIVE_ENABLED,
        CONF_ARCHIVE_SESSION_TIMEOUT_MINUTES: DEFAULT_ARCHIVE_SESSION_TIMEOUT_MINUTES,
        CONF_VOICE_SCOPE_POLICY: DEFAULT_VOICE_SCOPE_POLICY,
        CONF_VOICE_UNMAPPED_POLICY: DEFAULT_VOICE_UNMAPPED_POLICY,
        CONF_VOICE_DEFAULT_USER_ID: "",
        CONF_VOICE_DEVICE_MAPPINGS: {},
        CONF_SHARED_MEMORY_MODE: DEFAULT_SHARED_MEMORY_MODE,
        CONF_USAGE_REQUEST_RETENTION_DAYS: DEFAULT_USAGE_REQUEST_RETENTION_DAYS,
        CONF_USAGE_RUN_RETENTION_DAYS: DEFAULT_USAGE_RUN_RETENTION_DAYS,
        CONF_SHORTEN_TOOL_CALL_ID: DEFAULT_SHORTEN_TOOL_CALL_ID,
        CONF_ADVANCED_OPTIONS: DEFAULT_ADVANCED_OPTIONS,
        CONF_SKILLS: [],
        CONF_SPEECH_PROCESSING_ENABLED: DEFAULT_SPEECH_PROCESSING_ENABLED,
        CONF_SPEECH_STRIP_MARKDOWN: DEFAULT_SPEECH_STRIP_MARKDOWN,
        CONF_SPEECH_STRIP_URLS: DEFAULT_SPEECH_STRIP_URLS,
        CONF_SPEECH_REGEX_REPLACEMENTS: DEFAULT_SPEECH_REGEX_REPLACEMENTS,
    }
)

AGENT_CONFIG_FIELDS = frozenset(
    {*AGENT_CONFIG_DEFAULTS, CONF_REASONING_EFFORT, CONF_SERVICE_TIER}
)


def agent_config_defaults() -> dict[str, Any]:
    """Return an isolated copy of authoritative defaults."""
    return deepcopy(dict(AGENT_CONFIG_DEFAULTS))


def _choice(value: Any, label: str | None = None) -> dict[str, Any]:
    """Return one frontend-safe configuration choice."""
    return {
        "value": value,
        "label": label or str(value).replace("_", " ").replace("-", " ").title(),
    }


def agent_config_options() -> dict[str, list[dict[str, Any]]]:
    """Return authoritative option metadata for the management frontend."""
    return {
        CONF_API_MODE: [
            _choice(item["key"], str(item["label"])) for item in API_MODE_OPTIONS
        ],
        CONF_CONTINUE_CONVERSATION: [
            _choice(value) for value in CONTINUE_CONVERSATION_OPTIONS
        ],
        CONF_CONVERSATION_CONTINUITY: [
            _choice(value) for value in CONVERSATION_CONTINUITY_OPTIONS
        ],
        CONF_CONVERSATION_TIMEOUT_MINUTES: [
            _choice(
                value,
                (f"{value // 60} hour" if value == 60 else f"{value // 60} hours")
                if value >= 60
                else f"{value} minutes",
            )
            for value in CONVERSATION_TIMEOUT_OPTIONS
        ],
        CONF_WEB_SEARCH_CONTEXT: [
            _choice(value) for value in WEB_SEARCH_CONTEXT_OPTIONS
        ],
        CONF_MEMORY_MODE: [_choice(value) for value in MEMORY_MODES],
        CONF_TEMPORARY_MEMORY: [_choice(value) for value in TEMPORARY_MEMORY_OPTIONS],
        CONF_ARCHIVE_RETENTION_DAYS: [
            _choice(value, f"{value} days") for value in ARCHIVE_RETENTION_OPTIONS
        ],
        CONF_VOICE_SCOPE_POLICY: [_choice(value) for value in VOICE_POLICIES],
        CONF_VOICE_UNMAPPED_POLICY: [_choice(value) for value in VOICE_POLICIES],
        CONF_SHARED_MEMORY_MODE: [_choice(value) for value in SHARED_MEMORY_MODES],
        CONF_USAGE_REQUEST_RETENTION_DAYS: [
            _choice(value, f"{value} days" if value else "Disabled")
            for value in USAGE_RETENTION_OPTIONS
        ],
        CONF_USAGE_RUN_RETENTION_DAYS: [
            _choice(value, f"{value} days" if value else "Disabled")
            for value in USAGE_RETENTION_OPTIONS
        ],
        CONF_CONTEXT_TRUNCATE_STRATEGY: [
            _choice(item["key"], str(item["label"]))
            for item in CONTEXT_TRUNCATE_STRATEGIES
        ],
        CONF_REASONING_EFFORT: [_choice(value) for value in REASONING_EFFORT_OPTIONS],
        CONF_SERVICE_TIER: [_choice(value) for value in SERVICE_TIER_OPTIONS],
        "function_group_loading_modes": [
            _choice(value) for value in FUNCTION_GROUP_LOADING_MODES
        ],
    }


def validate_function_tools(value: Any) -> list[dict[str, Any]]:
    """Parse and validate function tools without executing them."""
    if isinstance(value, str):
        try:
            value = yaml.safe_load(value)
        except yaml.YAMLError as err:
            raise AgentConfigError(CONF_FUNCTION_TOOLS, f"invalid YAML: {err}") from err
    if value is None:
        return []
    if not isinstance(value, list):
        raise AgentConfigError(CONF_FUNCTION_TOOLS, "top-level value must be a list")
    result: list[dict[str, Any]] = []
    names: set[str] = set()
    for index, tool in enumerate(value):
        field = f"{CONF_FUNCTION_TOOLS}[{index}]"
        if not isinstance(tool, dict):
            raise AgentConfigError(field, "tool must be an object")
        if "enabled" in tool and not isinstance(tool["enabled"], bool):
            raise AgentConfigError(f"{field}.enabled", "must be a boolean")
        spec = tool.get("spec")
        function_config = tool.get("function")
        if not isinstance(spec, dict):
            raise AgentConfigError(f"{field}.spec", "must be an object")
        name = spec.get("name")
        if not isinstance(name, str) or not name.strip():
            raise AgentConfigError(f"{field}.spec.name", "is required")
        if name in names:
            raise AgentConfigError(f"{field}.spec.name", f"duplicate tool name: {name}")
        names.add(name)
        if not isinstance(function_config, dict):
            raise AgentConfigError(f"{field}.function", "must be an object")
        function_type = function_config.get("type")
        if not isinstance(function_type, str) or function_type not in FUNCTIONS:
            raise AgentConfigError(
                f"{field}.function.type", f"unrecognized function type: {function_type}"
            )
        try:
            get_function(function_type).validate_schema(deepcopy(function_config))
        except Exception as err:
            raise AgentConfigError(
                f"{field}.function",
                f"configuration is invalid for {function_type}: {err}",
            ) from err
        normalized = deepcopy(tool)
        normalized["spec"] = deepcopy(spec)
        normalized["function"] = deepcopy(function_config)
        result.append(normalized)
    return result


def function_tool_enabled(tool: dict[str, Any]) -> bool:
    """Return the single authoritative enabled state for a configured tool."""
    return tool.get("enabled", True) is True


def configured_function_tools_from_data(data: Any) -> list[dict[str, Any]]:
    """Parse fully validated production Function Tools from agent data."""
    configured = data.get(CONF_FUNCTION_TOOLS)
    parsed = yaml.safe_load(configured) if configured else DEFAULT_CONF_FUNCTION_TOOLS
    tools = validate_function_tools(parsed)
    for tool in tools:
        function_config = tool["function"]
        tool["function"] = get_function(function_config["type"]).validate_schema(
            function_config
        )
    return tools


_FUNCTION_GROUP_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


def validate_function_groups(
    value: Any, function_tools: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Validate compact group metadata and references to configured functions."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise AgentConfigError(CONF_FUNCTION_GROUPS, "must be a list")
    if len(value) > 50:
        raise AgentConfigError(CONF_FUNCTION_GROUPS, "supports at most 50 groups")

    tool_names = {tool["spec"]["name"] for tool in function_tools}
    group_ids: set[str] = set()
    group_names: set[str] = set()
    assigned_functions: dict[str, str] = {}
    result: list[dict[str, Any]] = []
    allowed = {"id", "name", "description", "loading_mode", "functions"}
    for index, group in enumerate(value):
        field = f"{CONF_FUNCTION_GROUPS}[{index}]"
        if not isinstance(group, dict):
            raise AgentConfigError(field, "group must be an object")
        unknown_fields = set(group) - allowed
        if unknown_fields:
            raise AgentConfigError(
                field, "unknown fields: " + ", ".join(sorted(unknown_fields))
            )
        group_id = group.get("id")
        if not isinstance(group_id, str) or not _FUNCTION_GROUP_ID.fullmatch(group_id):
            raise AgentConfigError(
                f"{field}.id",
                "must start with a lowercase letter and contain only lowercase "
                "letters, numbers, underscores, or hyphens (maximum 64 characters)",
            )
        if group_id in group_ids:
            raise AgentConfigError(f"{field}.id", f"duplicate group ID: {group_id}")
        group_ids.add(group_id)

        name = group.get("name")
        if not isinstance(name, str) or not name.strip():
            raise AgentConfigError(f"{field}.name", "is required")
        name = name.strip()
        if len(name) > 100:
            raise AgentConfigError(f"{field}.name", "must be at most 100 characters")
        normalized_name = name.casefold()
        if normalized_name in group_names:
            raise AgentConfigError(f"{field}.name", f"duplicate group name: {name}")
        group_names.add(normalized_name)

        description = group.get("description")
        if not isinstance(description, str) or not description.strip():
            raise AgentConfigError(f"{field}.description", "is required")
        description = description.strip()
        if len(description) > 500:
            raise AgentConfigError(
                f"{field}.description", "must be at most 500 characters"
            )

        loading_mode = group.get("loading_mode")
        if loading_mode not in FUNCTION_GROUP_LOADING_MODES:
            raise AgentConfigError(f"{field}.loading_mode", "unsupported value")
        functions = group.get("functions", [])
        if not isinstance(functions, list) or not all(
            isinstance(name, str) for name in functions
        ):
            raise AgentConfigError(f"{field}.functions", "must be a list of names")
        if len(functions) != len(set(functions)):
            raise AgentConfigError(f"{field}.functions", "contains duplicate names")
        for function_name in functions:
            if function_name not in tool_names:
                raise AgentConfigError(
                    f"{field}.functions", f"unknown function: {function_name}"
                )
            if function_name in assigned_functions:
                raise AgentConfigError(
                    f"{field}.functions",
                    f"function {function_name} is already assigned to group "
                    f"{assigned_functions[function_name]}",
                )
            assigned_functions[function_name] = group_id
        result.append(
            {
                "id": group_id,
                "name": name,
                "description": description,
                "loading_mode": loading_mode,
                "functions": list(functions),
            }
        )

    if (
        any(
            group["loading_mode"] == FUNCTION_GROUP_LOADING_ON_DEMAND
            for group in result
        )
        and FUNCTION_GROUP_LOADER_TOOL_NAME in tool_names
    ):
        raise AgentConfigError(
            CONF_FUNCTION_TOOLS,
            f"tool name `{FUNCTION_GROUP_LOADER_TOOL_NAME}` is reserved when an "
            "on-demand function group is configured",
        )
    return result


def validate_single_function_tool(value: Any) -> dict[str, Any]:
    """Parse and validate one function tool from a clean editor document."""
    if isinstance(value, str):
        try:
            value = yaml.safe_load(value)
        except yaml.YAMLError as err:
            raise AgentConfigError(CONF_FUNCTION_TOOLS, f"invalid YAML: {err}") from err
    if isinstance(value, list):
        raise AgentConfigError(
            CONF_FUNCTION_TOOLS,
            "single-tool YAML must contain an object, not a list",
        )
    return validate_function_tools([value])[0]


def function_tool_yaml(value: Any) -> str:
    """Serialize one validated function tool as readable YAML."""
    tool = validate_single_function_tool(value)
    return yaml.safe_dump(tool, sort_keys=False, allow_unicode=True)


def starter_function_tool_yaml() -> str:
    """Return the generic YAML-first editor template for a new function tool."""
    return yaml.safe_dump(
        {
            "spec": {
                "name": "my_tool",
                "description": "Describe what this tool does.",
                "parameters": {"type": "object", "properties": {}},
            },
            "function": {"type": "native", "name": ""},
        },
        sort_keys=False,
        allow_unicode=True,
    )


def validate_speech_regex_replacements(value: Any) -> list[dict[str, str]]:
    """Validate ordered spoken-text regex replacements."""
    if not isinstance(value, list):
        raise AgentConfigError(CONF_SPEECH_REGEX_REPLACEMENTS, "must be a list")
    if len(value) > MAX_SPEECH_REGEX_RULES:
        raise AgentConfigError(
            CONF_SPEECH_REGEX_REPLACEMENTS,
            f"supports at most {MAX_SPEECH_REGEX_RULES} rules",
        )
    result = []
    for index, rule in enumerate(value):
        field = f"{CONF_SPEECH_REGEX_REPLACEMENTS}[{index}]"
        if not isinstance(rule, dict):
            raise AgentConfigError(field, "must be an object")
        unknown = set(rule) - {"pattern", "replacement"}
        if unknown:
            raise AgentConfigError(
                field, "unknown fields: " + ", ".join(sorted(unknown))
            )
        pattern = rule.get("pattern")
        replacement = rule.get("replacement")
        if not isinstance(pattern, str) or not pattern:
            raise AgentConfigError(f"{field}.pattern", "is required")
        if not isinstance(replacement, str):
            raise AgentConfigError(f"{field}.replacement", "must be a string")
        if len(pattern) > MAX_SPEECH_REGEX_PATTERN_LENGTH:
            raise AgentConfigError(f"{field}.pattern", "is too long")
        if len(replacement) > MAX_SPEECH_REGEX_REPLACEMENT_LENGTH:
            raise AgentConfigError(f"{field}.replacement", "is too long")
        try:
            compiled = re.compile(pattern)
        except re.error as err:
            raise AgentConfigError(
                f"{field}.pattern", f"invalid regular expression: {err}"
            ) from err
        try:
            compiled.sub(replacement, "")
        except re.error as err:
            raise AgentConfigError(
                f"{field}.replacement", f"invalid replacement expression: {err}"
            ) from err
        result.append({"pattern": pattern, "replacement": replacement})
    return result


def _require_type(
    config: dict[str, Any], keys: tuple[str, ...], expected: type | tuple[type, ...]
) -> None:
    label = (
        expected.__name__
        if isinstance(expected, type)
        else " or ".join(item.__name__ for item in expected)
    )
    for key in keys:
        if key in config and (
            not isinstance(config[key], expected)
            or (expected is int and isinstance(config[key], bool))
        ):
            raise AgentConfigError(key, f"must be a {label}")


def _coerce_legacy_numbers(config: dict[str, Any]) -> None:
    """Normalize numeric values stored as strings by older selector flows."""
    integer_keys = (
        CONF_MAX_TOKENS,
        CONF_MAX_FUNCTION_CALLS_PER_CONVERSATION,
        CONF_CONTEXT_THRESHOLD,
        CONF_ARCHIVE_SESSION_TIMEOUT_MINUTES,
        CONF_MEMORY_AUTO_RETRIEVE_LIMIT,
        CONF_ARCHIVE_RETENTION_DAYS,
        CONF_USAGE_REQUEST_RETENTION_DAYS,
        CONF_USAGE_RUN_RETENTION_DAYS,
    )
    for key in integer_keys:
        value = config.get(key)
        if isinstance(value, str):
            stripped = value.strip()
            if re.fullmatch(r"[+-]?\d+(?:\.0+)?", stripped):
                config[key] = int(stripped.split(".", maxsplit=1)[0])
        elif isinstance(value, float) and value.is_integer():
            config[key] = int(value)

    for key in (CONF_TOP_P, CONF_TEMPERATURE):
        value = config.get(key)
        if isinstance(value, str):
            try:
                config[key] = float(value.strip())
            except ValueError:
                continue


def normalize_agent_config(
    data: dict[str, Any], *, apply_defaults: bool = True, reject_unknown: bool = True
) -> dict[str, Any]:
    """Validate and normalize a complete or partial agent configuration."""
    if not isinstance(data, dict):
        raise AgentConfigError("config", "must be an object")
    unknown = set(data) - AGENT_CONFIG_FIELDS
    if reject_unknown and unknown:
        raise AgentConfigError(
            "config", "unknown fields: " + ", ".join(sorted(unknown))
        )
    result = agent_config_defaults() if apply_defaults else {}
    result.update(deepcopy(data))
    _coerce_legacy_numbers(result)

    _require_type(
        result,
        (
            CONF_ARCHIVE_ENABLED,
            CONF_ARCHIVE_MODEL_SEARCH_ENABLED,
            CONF_SHARED_ARCHIVE_ENABLED,
            CONF_WEB_SEARCH,
            CONF_KNOWLEDGE_ENABLED,
            CONF_SHORTEN_TOOL_CALL_ID,
            CONF_ADVANCED_OPTIONS,
            CONF_CURRENT_DATETIME_ENABLED,
            CONF_EXPOSED_ENTITIES_ENABLED,
            CONF_SPEECH_PROCESSING_ENABLED,
            CONF_SPEECH_STRIP_MARKDOWN,
            CONF_SPEECH_STRIP_URLS,
        ),
        bool,
    )
    _require_type(
        result,
        (
            CONF_PROMPT,
            CONF_CURRENT_DATETIME_TEMPLATE,
            CONF_EXPOSED_ENTITIES_TEMPLATE,
            CONF_CHAT_MODEL,
            CONF_API_MODE,
            CONF_CONTEXT_TRUNCATE_STRATEGY,
            CONF_CONTINUE_CONVERSATION,
            CONF_CONVERSATION_CONTINUITY,
            CONF_MEMORY_MODE,
            CONF_TEMPORARY_MEMORY,
            CONF_VOICE_SCOPE_POLICY,
            CONF_VOICE_UNMAPPED_POLICY,
            CONF_SHARED_MEMORY_MODE,
        ),
        str,
    )
    _require_type(
        result,
        (
            CONF_MAX_TOKENS,
            CONF_MAX_FUNCTION_CALLS_PER_CONVERSATION,
            CONF_CONTEXT_THRESHOLD,
            CONF_CONVERSATION_TIMEOUT_MINUTES,
            CONF_ARCHIVE_SESSION_TIMEOUT_MINUTES,
            CONF_MEMORY_AUTO_RETRIEVE_LIMIT,
        ),
        int,
    )
    _require_type(result, (CONF_TOP_P, CONF_TEMPERATURE), (int, float))

    choices: dict[str, list[Any]] = {
        CONF_API_MODE: [item["key"] for item in API_MODE_OPTIONS],
        CONF_CONTINUE_CONVERSATION: CONTINUE_CONVERSATION_OPTIONS,
        CONF_CONVERSATION_CONTINUITY: CONVERSATION_CONTINUITY_OPTIONS,
        CONF_CONVERSATION_TIMEOUT_MINUTES: CONVERSATION_TIMEOUT_OPTIONS,
        CONF_WEB_SEARCH_CONTEXT: WEB_SEARCH_CONTEXT_OPTIONS,
        CONF_MEMORY_MODE: MEMORY_MODES,
        CONF_TEMPORARY_MEMORY: TEMPORARY_MEMORY_OPTIONS,
        CONF_ARCHIVE_RETENTION_DAYS: ARCHIVE_RETENTION_OPTIONS,
        CONF_VOICE_SCOPE_POLICY: VOICE_POLICIES,
        CONF_VOICE_UNMAPPED_POLICY: VOICE_POLICIES,
        CONF_SHARED_MEMORY_MODE: SHARED_MEMORY_MODES,
        CONF_USAGE_REQUEST_RETENTION_DAYS: USAGE_RETENTION_OPTIONS,
        CONF_USAGE_RUN_RETENTION_DAYS: USAGE_RETENTION_OPTIONS,
        CONF_CONTEXT_TRUNCATE_STRATEGY: [
            item["key"] for item in CONTEXT_TRUNCATE_STRATEGIES
        ],
        CONF_REASONING_EFFORT: REASONING_EFFORT_OPTIONS,
        CONF_SERVICE_TIER: SERVICE_TIER_OPTIONS,
    }
    for key, options in choices.items():
        if key in result and result[key] not in options:
            message = (
                "unsupported archive retention"
                if key == CONF_ARCHIVE_RETENTION_DAYS
                else "unsupported value"
            )
            raise AgentConfigError(key, message)
    if result[CONF_MAX_TOKENS] < 1:
        raise AgentConfigError(CONF_MAX_TOKENS, "must be at least 1")
    if result[CONF_MAX_FUNCTION_CALLS_PER_CONVERSATION] < 0:
        raise AgentConfigError(
            CONF_MAX_FUNCTION_CALLS_PER_CONVERSATION, "must be at least 0"
        )
    if result[CONF_CONTEXT_THRESHOLD] < 1:
        raise AgentConfigError(CONF_CONTEXT_THRESHOLD, "must be at least 1")
    if not 1 <= result[CONF_ARCHIVE_SESSION_TIMEOUT_MINUTES] <= 1440:
        raise AgentConfigError(
            CONF_ARCHIVE_SESSION_TIMEOUT_MINUTES, "must be 1 to 1440"
        )
    if (
        not 0
        <= result[CONF_MEMORY_AUTO_RETRIEVE_LIMIT]
        <= MAX_MEMORY_AUTO_RETRIEVE_LIMIT
    ):
        raise AgentConfigError(
            CONF_MEMORY_AUTO_RETRIEVE_LIMIT,
            f"must be 0 to {MAX_MEMORY_AUTO_RETRIEVE_LIMIT}",
        )
    if not 0 <= float(result[CONF_TOP_P]) <= 1:
        raise AgentConfigError(CONF_TOP_P, "must be 0 to 1")
    if not 0 <= float(result[CONF_TEMPERATURE]) <= 2:
        raise AgentConfigError(CONF_TEMPERATURE, "must be 0 to 2")
    for key in (CONF_CURRENT_DATETIME_TEMPLATE, CONF_EXPOSED_ENTITIES_TEMPLATE):
        value = result[key]
        if not value.strip():
            continue
        try:
            template.Template(value, cast(Any, None)).ensure_valid()
        except Exception as err:
            concise = " ".join(str(err).split())[:300] or type(err).__name__
            raise AgentConfigError(key, f"invalid template: {concise}") from err
    mappings = result.get(CONF_VOICE_DEVICE_MAPPINGS, {})
    if not isinstance(mappings, dict) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in mappings.items()
    ):
        raise AgentConfigError(
            CONF_VOICE_DEVICE_MAPPINGS, "must map device IDs to scope owners"
        )
    skills = result.get(CONF_SKILLS, [])
    if not isinstance(skills, list) or not all(
        isinstance(skill, str) for skill in skills
    ):
        raise AgentConfigError(CONF_SKILLS, "must be a list of names")

    function_tools = validate_function_tools(result.get(CONF_FUNCTION_TOOLS, []))
    result[CONF_FUNCTION_GROUPS] = validate_function_groups(
        result.get(CONF_FUNCTION_GROUPS, []), function_tools
    )
    if CONF_FUNCTION_TOOLS in data:
        result[CONF_FUNCTION_TOOLS] = yaml.safe_dump(
            function_tools, sort_keys=False, allow_unicode=True
        )
    result[CONF_SPEECH_REGEX_REPLACEMENTS] = validate_speech_regex_replacements(
        result.get(CONF_SPEECH_REGEX_REPLACEMENTS, [])
    )
    mode = get_memory_mode(result)
    result[CONF_MEMORY_MODE] = mode
    result[CONF_MEMORY_ENABLED] = mode != "off"
    result[CONF_MEMORY_AUTO_CREATE] = mode == "automatic"
    return result


def merge_agent_config(
    current: dict[str, Any], updates: dict[str, Any]
) -> dict[str, Any]:
    """Validate updates against the final merged configuration."""
    unknown = set(updates) - AGENT_CONFIG_FIELDS
    if unknown:
        raise AgentConfigError(
            "config", "unknown fields: " + ", ".join(sorted(unknown))
        )
    known = {key: value for key, value in current.items() if key in AGENT_CONFIG_FIELDS}
    normalized = normalize_agent_config({**known, **updates})
    return {
        **{
            key: deepcopy(value)
            for key, value in current.items()
            if key not in AGENT_CONFIG_FIELDS
        },
        **normalized,
    }


def agent_config_snapshot(data: dict[str, Any]) -> dict[str, Any]:
    """Return frontend-safe normalized configuration with parsed tools."""
    result = normalize_agent_config(
        {key: value for key, value in data.items() if key in AGENT_CONFIG_FIELDS}
    )
    result[CONF_FUNCTION_TOOLS] = validate_function_tools(result[CONF_FUNCTION_TOOLS])
    result[CONF_FUNCTION_GROUPS] = validate_function_groups(
        result[CONF_FUNCTION_GROUPS], result[CONF_FUNCTION_TOOLS]
    )
    return result


def model_capabilities(model: str) -> dict[str, bool]:
    """Return model-specific fields supported by the backend."""
    return dict(get_model_config(model))
