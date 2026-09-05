"""Safe, minimal conversation-agent configuration test."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, cast

from openai import AuthenticationError, OpenAIError
import yaml

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant

from .agent_config import configured_function_tools_from_data
from .const import (
    API_MODE_CHAT_COMPLETIONS,
    API_MODE_RESPONSES,
    CONF_API_MODE,
    CONF_API_PROVIDER,
    CONF_BASE_URL,
    CONF_CHAT_MODEL,
    CONF_FUNCTION_TOOLS,
    CONF_SKILLS,
    CONF_WEB_SEARCH,
    DEFAULT_API_MODE,
    DEFAULT_CHAT_MODEL,
    DEFAULT_CONF_FUNCTION_TOOLS,
    DEFAULT_WEB_SEARCH,
)
from .functions import get_function
from .guest_mode import get_loaded_guest_mode, resolve_guest_policy
from .helpers import (
    get_api_mode,
    get_exposed_entities,
    get_model_config,
    supports_openai_hosted_tools,
)
from .memory import async_get_memory, memory_enabled
from .provider_errors import ensure_successful_responses_result, provider_user_message
from .usage import async_get_usage, extract_usage


@dataclass(slots=True)
class TestCheck:
    """One human-readable agent test check."""

    name: str
    status: str
    message: str


@dataclass(slots=True)
class AgentTestResult:
    """Structured overall test result."""

    status: str
    checks: list[TestCheck]

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-safe response for flows and WebSocket clients."""
        return {
            "status": self.status,
            "checks": [asdict(check) for check in self.checks],
        }

    def as_text(self) -> str:
        """Return a concise result suitable for a native Home Assistant form."""
        return "\n".join(
            [f"Overall: {self.status}"]
            + [
                f"{check.name}: {check.status} — {check.message}"
                for check in self.checks
            ]
        )


def _check(name: str, status: str, message: str) -> TestCheck:
    return TestCheck(name=name, status=status, message=message)


def _overall(checks: list[TestCheck]) -> str:
    if any(check.status == "Failed" for check in checks):
        return "Failed"
    if any(check.status == "Warning" for check in checks):
        return "Warning"
    return "Passed"


def _validate_function_schema(subentry: ConfigSubentry) -> int:
    """Validate configured tool schemas without executing any tool."""
    configured = subentry.data.get(CONF_FUNCTION_TOOLS)
    tools = yaml.safe_load(configured) if configured else DEFAULT_CONF_FUNCTION_TOOLS
    for tool in tools or []:
        if not isinstance(tool, dict) or not isinstance(tool.get("function"), dict):
            raise ValueError("Each function tool must contain a function mapping")
        function_config = cast(dict[str, Any], tool["function"])
        get_function(function_config["type"]).validate_schema(function_config)
    return len(tools or [])


async def async_test_agent(
    hass: HomeAssistant,
    entry: ConfigEntry,
    subentry: ConfigSubentry,
) -> AgentTestResult:
    """Test one agent with local checks and at most one minimal live request."""
    checks: list[TestCheck] = []
    client = getattr(entry, "runtime_data", None)
    if client is None:
        checks.append(_check("Authentication", "Failed", "API client is unavailable"))
        return AgentTestResult(_overall(checks), checks)
    checks.append(_check("Authentication", "Passed", "API client is available"))

    model = subentry.data.get(CONF_CHAT_MODEL, DEFAULT_CHAT_MODEL)
    configured_mode = subentry.data.get(CONF_API_MODE, DEFAULT_API_MODE)
    api_mode = get_api_mode(configured_mode, model)
    if api_mode not in {API_MODE_CHAT_COMPLETIONS, API_MODE_RESPONSES}:
        checks.append(_check("API mode", "Failed", f"Unsupported mode: {api_mode}"))
        return AgentTestResult(_overall(checks), checks)
    checks.append(_check("API mode", "Passed", api_mode.replace("_", " ").title()))

    try:
        function_count = _validate_function_schema(subentry)
    except Exception as err:
        checks.append(_check("Configuration", "Failed", str(err)))
        return AgentTestResult(_overall(checks), checks)
    checks.append(
        _check("Configuration", "Passed", f"{function_count} tool schemas valid")
    )
    guest_mode = get_loaded_guest_mode(hass, entry.entry_id, subentry.subentry_id)
    guest_status = (
        guest_mode.status() if guest_mode is not None else {"state": "inactive"}
    )
    guest_policy = resolve_guest_policy(
        hass,
        subentry.data,
        guest_mode,
        configured_function_tools_from_data(subentry.data),
    )
    policy = guest_policy.as_diagnostics()
    checks.append(
        _check(
            "Guest Mode",
            "Passed",
            (
                f"{str(guest_status['state']).replace('_', ' ').title()}; "
                f"{policy['readable_entity_count'] or 0} visible entities; "
                f"{policy['configured_tool_count'] or 0} custom tools"
                if guest_policy.guest_active
                else "Inactive"
            ),
        )
    )

    try:
        entity_count = len(get_exposed_entities(hass))
    except Exception:
        entity_count = 0
    checks.append(
        _check(
            "Exposed entities",
            "Passed" if entity_count else "Warning",
            str(entity_count),
        )
    )
    checks.append(
        _check(
            "Skills",
            "Passed",
            f"{len(subentry.data.get(CONF_SKILLS, []) or [])} enabled",
        )
    )

    if memory_enabled(subentry.data):
        try:
            memory = await async_get_memory(hass, entry.entry_id, subentry.subentry_id)
            checks.append(
                _check(
                    "Persistent memory",
                    "Passed",
                    f"Available ({memory.stats()['memory_count']} stored)",
                )
            )
        except Exception as err:
            checks.append(_check("Persistent memory", "Failed", type(err).__name__))
    else:
        checks.append(_check("Persistent memory", "Passed", "Disabled"))

    web_search = subentry.data.get(CONF_WEB_SEARCH, DEFAULT_WEB_SEARCH)
    web_search_compatible = (
        api_mode == API_MODE_RESPONSES
        and supports_openai_hosted_tools(
            entry.data.get(CONF_API_PROVIDER), entry.data.get(CONF_BASE_URL)
        )
    )
    if web_search and not web_search_compatible:
        checks.append(
            _check(
                "Web Search",
                "Failed",
                "Enabled, but this API mode or provider does not support hosted OpenAI Web Search",
            )
        )
    elif not web_search:
        checks.append(_check("Web Search", "Passed", "Disabled"))

    noop_tool = {
        "type": "function",
        "name": "configuration_test_noop",
        "description": "Schema-only compatibility test; never execute this function.",
        "parameters": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "strict": True,
    }
    usage_manager = await async_get_usage(hass, entry.entry_id, subentry.subentry_id)
    try:
        if api_mode == API_MODE_RESPONSES:
            tools: list[dict[str, Any]] = [noop_tool]
            if web_search and web_search_compatible:
                tools.insert(0, {"type": "web_search", "search_context_size": "low"})
            response = await client.responses.create(
                model=model,
                input=[{"role": "user", "content": "Reply OK."}],
                max_output_tokens=16,
                store=False,
                tools=tools,
                tool_choice="none",
            )
            ensure_successful_responses_result(response)
        else:
            model_config = get_model_config(model)
            kwargs: dict[str, Any] = {
                "model": model,
                "messages": [{"role": "user", "content": "Reply OK."}],
                "stream": False,
                "tools": [
                    {
                        "type": "function",
                        "function": {
                            key: value
                            for key, value in noop_tool.items()
                            if key != "type"
                        },
                    }
                ],
                "tool_choice": "none",
            }
            if model_config["supports_max_completion_tokens"]:
                kwargs["max_completion_tokens"] = 16
            else:
                kwargs["max_tokens"] = 16
            response = await client.chat.completions.create(**kwargs)
    except AuthenticationError as err:
        await usage_manager.async_record_request(successful=False)
        authentication = next(
            check for check in checks if check.name == "Authentication"
        )
        authentication.status = "Failed"
        authentication.message = provider_user_message(err)
        checks.append(_check("Model access", "Failed", "Authentication rejected"))
        checks.append(_check("Function calling", "Failed", "Probe was rejected"))
    except OpenAIError as err:
        await usage_manager.async_record_request(successful=False)
        message = provider_user_message(err)
        checks.append(_check("Model access", "Failed", message))
        checks.append(_check("Function calling", "Failed", "Probe was rejected"))
        if web_search and web_search_compatible:
            checks.append(_check("Web Search", "Failed", message))
    except Exception as err:
        await usage_manager.async_record_request(successful=False)
        checks.append(_check("Model access", "Failed", str(err)))
        checks.append(_check("Function calling", "Failed", "Probe was rejected"))
        if web_search and web_search_compatible:
            checks.append(_check("Web Search", "Failed", str(err)))
    else:
        await usage_manager.async_record_request(
            successful=True, usage=extract_usage(getattr(response, "usage", None))
        )
        checks.append(
            _check("Model access", "Passed", f"Minimal {model} request succeeded")
        )
        checks.append(_check("Function calling", "Passed", "Function schema accepted"))
        if web_search and web_search_compatible:
            checks.append(_check("Web Search", "Passed", "Hosted tool schema accepted"))

    return AgentTestResult(_overall(checks), checks)
