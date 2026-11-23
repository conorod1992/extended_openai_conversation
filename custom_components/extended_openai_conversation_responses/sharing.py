"""Allowlisted public sharing, separate from private recovery serialization."""

from __future__ import annotations

from copy import deepcopy
import math
from typing import Any

from .const import CONTEXT_TRUNCATE_STRATEGIES, SERVICE_TIER_OPTIONS
from .function_yaml import decoded_function_fields
from .model_catalog import BUNDLED_CATALOG, REASONING_EFFORT_VALUES

SHARING_VERSION = 2
SECURITY_CONTRACT = (
    "New shareable exports include supported public settings and omit provider "
    "credentials, private data and custom content."
)
# These are semantic decisions, not a rule that every boolean/number is public.
PUBLIC_BOOLEANS = frozenset(
    [
        "current_datetime_enabled",
        "exposed_entities_enabled",
        "function_tool_error_recovery",
        "local_intents_enabled",
        "local_intent_delayed_commands_to_ai",
        "web_search",
        "memory_enabled",
        "memory_auto_create",
        "knowledge_enabled",
        "guest_mode_enabled",
        "guest_web_search",
        "guest_separate_control_restrictions",
        "guest_shared_memory_read",
        "guest_shared_memory_write",
        "guest_knowledge_enabled",
        "archive_enabled",
        "archive_model_search_enabled",
        "shared_archive_enabled",
        "shorten_tool_call_id",
        "advanced_options",
        "speech_processing_enabled",
        "speech_strip_markdown",
        "speech_strip_urls",
    ]
)
PUBLIC_NUMBERS = frozenset(
    [
        "max_tokens",
        "max_function_calls_per_conversation",
        "top_p",
        "temperature",
        "context_threshold",
        "conversation_timeout_minutes",
        "memory_auto_retrieve_limit",
        "archive_retention_days",
        "archive_session_timeout_minutes",
        "usage_request_retention_days",
        "usage_run_retention_days",
        "guest_policy_version",
    ]
)
PUBLIC_ENUMS = {
    "chat_model": frozenset(BUNDLED_CATALOG.resolved),
    "api_mode": {"auto", "responses", "chat_completions"},
    "reasoning_effort": REASONING_EFFORT_VALUES | {""},
    "service_tier": frozenset(SERVICE_TIER_OPTIONS) | {""},
    "context_truncate_strategy": frozenset(
        item["key"] for item in CONTEXT_TRUNCATE_STRATEGIES
    ),
    "memory_mode": {"off", "manual", "automatic"},
    "guest_knowledge_policy": {"off", "on", "custom"},
    "guest_function_policy": {"off", "on", "custom"},
    "guest_shared_memory_policy": {"off", "read_only", "read_write"},
}


def security_summary() -> dict[str, Any]:
    return {
        "policy_version": 1,
        "mode": "restricted",
        "contract": SECURITY_CONTRACT,
        "review_required": False,
        "omitted": [],
    }


def record_omission(summary: dict[str, Any], field: str, *, count: int = 1) -> None:
    """Callers supply only fixed policy labels, never source keys/values."""
    summary["omitted"].append(
        {"field": field, "reason": "requires_private_configuration", "count": count}
    )


def project_sharing_sections(
    sections: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Project the supported sections before enclosing them in a public artifact."""
    projected: dict[str, Any] = {}
    summary = security_summary()
    if "configuration" in sections:
        projected["configuration"], summary = project_configuration(
            sections["configuration"]
        )
    if "request_rules" in sections:
        projected["request_rules"] = project_rules(sections["request_rules"], summary)
    if sections.keys() - {"configuration", "request_rules"}:
        record_omission(summary, "unclassified_section")
    return projected, summary


def project_configuration(
    config: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return allowlisted public settings and a value-free omission manifest."""
    from .agent_config import AGENT_CONFIG_FIELDS

    summary = security_summary()
    result: dict[str, Any] = {"functions": [], "function_groups": [], "skills": []}
    decoded = decoded_function_fields(config)
    for key, value in decoded.items():
        public = (
            (key in PUBLIC_BOOLEANS and type(value) is bool)
            or (
                key in PUBLIC_NUMBERS
                and type(value) in (int, float)
                and math.isfinite(value)
            )
            or (
                key in PUBLIC_ENUMS
                and isinstance(value, str)
                and value in PUBLIC_ENUMS[key]
            )
        )
        if public:
            result[key] = deepcopy(value)
        elif key != "functions":
            record_omission(
                summary,
                key
                if key in AGENT_CONFIG_FIELDS
                else "unclassified_configuration_field",
            )
    tools = decoded.get("functions", [])
    if isinstance(tools, list):
        for index in range(len(tools)):
            record_omission(summary, f"function_tool_{index + 1}")
    elif tools:
        record_omission(summary, "function_tool")
    if "chat_model" not in result:
        result.pop("reasoning_effort", None)
        result.pop("service_tier", None)
    return result, summary


def project_rules(data: dict[str, Any], summary: dict[str, Any]) -> dict[str, Any]:
    """Omit authored rules, groups, and matching metadata from public sharing."""
    from .request_rules import DEFAULT_MATCHING, DEFAULT_WORDING_GROUPS

    if rules := data.get("rules"):
        record_omission(summary, "request_rule", count=len(rules))
    storage_version = data.get("storage_version")
    return {
        "rules": [],
        "groups": [],
        **(
            {"storage_version": storage_version} if type(storage_version) is int else {}
        ),
        **({"defaults": dict(DEFAULT_MATCHING)} if "defaults" in data else {}),
        **(
            {"wording_groups": deepcopy(DEFAULT_WORDING_GROUPS)}
            if "wording_groups" in data
            else {}
        ),
    }
