"""Positive sharing projection, separate from private recovery serialization.

Unknown content is never inferred public from its spelling or apparent entropy.
Reviewed sharing is an explicit author decision, not a secret-free guarantee.
"""

from __future__ import annotations

from copy import deepcopy
import json
import math
from typing import Any

from .function_yaml import decoded_function_fields
from .model_catalog import BUNDLED_CATALOG
from .secret_redaction import _is_secret_key, is_redacted_secret, redact_secrets

SHARING_VERSION = 2
SECURITY_CONTRACT = (
    "Shareable exports exclude designated credentials and unclassified custom "
    "content by default, and check included content for recognized secret patterns. "
    "Custom content you choose to include may still contain secrets. Review the "
    "summary and file before sharing, especially Function Tools, scripts and templates."
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
    "reasoning_effort": {"", "none", "minimal", "low", "medium", "high", "xhigh"},
    "service_tier": {"", "auto", "default", "flex", "priority"},
    "context_truncate_strategy": {"clear", "truncate"},
    "memory_mode": {"off", "manual", "automatic"},
    "guest_knowledge_policy": {"off", "on", "custom"},
    "guest_function_policy": {"off", "on", "custom"},
    "guest_shared_memory_policy": {"off", "read_only", "read_write"},
}


def security_summary(reviewed: bool) -> dict[str, Any]:
    return {
        "policy_version": 1,
        "mode": "reviewed_custom" if reviewed else "restricted",
        "contract": SECURITY_CONTRACT,
        "review_required": reviewed,
        "omitted": [],
    }


def record_omission(summary: dict[str, Any], field: str, *, count: int = 1) -> None:
    """Callers supply only fixed policy labels, never source keys/values."""
    summary["omitted"].append(
        {"field": field, "reason": "requires_private_configuration", "count": count}
    )


def _unsafe(value: Any) -> bool:
    if is_redacted_secret(value):
        return True
    if isinstance(value, dict):
        return any(
            not isinstance(key, str)
            or is_redacted_secret(redact_secrets(key))
            or _unsafe(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_unsafe(item) for item in value)
    return not (
        value is None
        or isinstance(value, (str, bool, int))
        or (isinstance(value, float) and math.isfinite(value))
    )


def _structured_payloads(value: Any) -> Any:
    """Do not put redaction markers inside executable opaque request strings."""
    if isinstance(value, list):
        return [_structured_payloads(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        if key == "properties" and isinstance(item, dict):
            item = deepcopy(item)
            for name, definition in item.items():
                if _is_secret_key(name) and isinstance(definition, dict):
                    for annotation in {
                        "default",
                        "examples",
                        "enum",
                        "const",
                    } & definition.keys():
                        from .secret_redaction import REDACTED_SECRET_SENTINEL

                        definition[annotation] = dict(REDACTED_SECRET_SENTINEL)
        if key in {"payload", "payload_template"} and isinstance(item, str):
            try:
                structured = json.loads(item)
            except ValueError, RecursionError:
                pass  # Arbitrary reviewed source remains explicitly uncertain.
            else:
                if _unsafe(redact_secrets(structured)):
                    from .secret_redaction import REDACTED_SECRET_SENTINEL

                    result[key] = dict(REDACTED_SECRET_SENTINEL)
                    continue
        result[key] = _structured_payloads(item)
    return result


def project_configuration(
    config: dict[str, Any], *, reviewed: bool = False
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return a valid partial configuration and a value-free omission manifest."""
    from .agent_config import AGENT_CONFIG_FIELDS

    summary = security_summary(reviewed)
    result: dict[str, Any] = {"functions": [], "function_groups": [], "skills": []}
    decoded = decoded_function_fields(config)
    redacted = _structured_payloads(redact_secrets(decoded)) if reviewed else {}
    # Typed YAML is valid private data, but not a supported reviewed public codec.
    if isinstance(redacted.get("functions"), str):
        redacted["functions"] = []
        record_omission(summary, "typed_function_content")
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
        elif reviewed and key in AGENT_CONFIG_FIELDS and key != "functions":
            candidate = redacted.get(key)
            if not _unsafe(candidate):
                result[key] = deepcopy(candidate)
            else:
                record_omission(summary, key)
        elif key != "functions":
            record_omission(
                summary,
                key
                if key in AGENT_CONFIG_FIELDS
                else "unclassified_configuration_field",
            )
    tools = decoded.get("functions", [])
    if isinstance(tools, list):
        candidates = redacted.get("functions", []) if reviewed else []
        for index, _tool in enumerate(tools):
            candidate = candidates[index] if index < len(candidates) else None
            if reviewed and candidate is not None and not _unsafe(candidate):
                result["functions"].append(deepcopy(candidate))
            else:
                record_omission(summary, f"function_tool_{index + 1}")
        if len(result["functions"]) != len(tools):
            result["function_groups"] = []
    elif tools:
        record_omission(summary, "function_tool")
    # Skills contain arbitrary external/custom content and dependency references.
    if not reviewed:
        result["function_groups"] = []
        if "chat_model" not in result:
            result.pop("reasoning_effort", None)
            result.pop("service_tier", None)
    return result, summary


def project_rules(
    data: dict[str, Any], summary: dict[str, Any], *, reviewed: bool
) -> dict[str, Any]:
    """Public rule bodies are coherent review units, never guessed safe prose."""
    result = {
        key: deepcopy(value)
        for key, value in data.items()
        if key in {"rules", "groups", "storage_version", "defaults", "wording_groups"}
    }
    rules = result.get("rules", [])
    retained = []
    if reviewed:
        for rule in rules:
            candidate = _structured_payloads(redact_secrets({"rules": [rule]}))[
                "rules"
            ][0]
            if not _unsafe(candidate):
                retained.append(candidate)
            else:
                record_omission(summary, "request_rule")
    elif rules:
        record_omission(summary, "request_rule", count=len(rules))
    for index, rule in enumerate(retained):
        rule["order"] = index
        rule["enabled"] = False
    result["rules"] = retained
    if not reviewed:
        result["groups"] = []
        # These strings/labels are authored content too, even with no rules.
        from .request_rules import DEFAULT_MATCHING, DEFAULT_WORDING_GROUPS

        if "defaults" in result:
            result["defaults"] = dict(DEFAULT_MATCHING)
        if "wording_groups" in result:
            result["wording_groups"] = deepcopy(DEFAULT_WORDING_GROUPS)
    elif _unsafe(redact_secrets(result)):
        # Includes credential-bearing group/wording metadata.
        record_omission(summary, "rule_metadata")
        return project_rules(data, summary, reviewed=False)
    return result
