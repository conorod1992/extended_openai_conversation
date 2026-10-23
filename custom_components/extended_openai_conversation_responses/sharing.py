"""Positive sharing projection, separate from private recovery serialization.

Unknown content is never inferred public from its spelling or apparent entropy.
Reviewed sharing is an explicit author decision, not a secret-free guarantee.
"""

from __future__ import annotations

from copy import deepcopy
import json
import math
import re
from typing import Any
from urllib.parse import unquote, urlsplit, urlunsplit

from .function_yaml import decoded_function_fields
from .model_catalog import BUNDLED_CATALOG
from .secret_redaction import (
    REDACTED_SECRET_SENTINEL,
    _is_function_tool_schema_root,
    _is_secret_key,
    is_redacted_secret,
    redact_http_url,
    redact_secrets,
)

SHARING_VERSION = 2
_SCHEMA_REFERENCES = frozenset({"$ref", "$dynamicRef", "$recursiveRef"})
_SCHEMA_URI_KEYWORDS = _SCHEMA_REFERENCES | {"$id", "$schema", "id"}
_SCHEMA_MAP_KEYWORDS = frozenset(
    {
        "properties",
        "patternProperties",
        "$defs",
        "definitions",
        "dependentSchemas",
        "dependencies",
    }
)
_SCHEMA_INSTANCE_KEYWORDS = frozenset(
    {"default", "example", "examples", "enum", "const"}
)
_INVALID_PERCENT_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})")
_INVALID_POINTER_ESCAPE = re.compile(r"~(?:[^01]|$)")
type _ReferenceKey = tuple[int, tuple[str, ...]]
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


def _schema_reference_uri(value: Any) -> Any:
    """Check all URI-reference forms with the shared HTTP credential policy.

    Relative, network-path and custom-scheme references can carry the same
    query credentials as HTTP URLs. The synthetic scheme/authority is used only
    for classification, never resolution, transport or the exported value.
    """
    if not isinstance(value, str):
        return dict(REDACTED_SECRET_SENTINEL)
    try:
        parsed = urlsplit(value)
    except ValueError:
        return dict(REDACTED_SECRET_SENTINEL)
    candidate = urlunsplit(
        (
            "https",
            parsed.netloc or "schema.invalid",
            parsed.path,
            parsed.query,
            parsed.fragment,
        )
    )
    return (
        dict(REDACTED_SECRET_SENTINEL)
        if redact_http_url(candidate) is not None
        else value
    )


def _single_schema_resource(root: Any) -> bool:
    """Prove the local-pointer subset without guessing resource resolution.

    Embedded resource identities need a dialect-aware resolver. Conservatively
    omit sensitive references in those schemas rather than inspecting a shadow
    target in the outer resource. Walk shared containers once, without expansion.
    """
    pending = [iter(((root, True),))]
    seen: set[tuple[int, bool]] = set()
    while pending:
        try:
            value, schema_node = next(pending[-1])
        except StopIteration:
            pending.pop()
            continue
        identity = (id(value), schema_node)
        if not isinstance(value, (dict, list)) or identity in seen:
            continue
        seen.add(identity)
        if isinstance(value, dict):
            if (
                schema_node
                and value is not root
                and any(isinstance(value.get(key), str) for key in ("$id", "id"))
            ):
                return False
            if schema_node:
                pending.append(
                    iter(
                        (item, key not in _SCHEMA_MAP_KEYWORDS)
                        for key, item in value.items()
                        if key not in _SCHEMA_INSTANCE_KEYWORDS
                    )
                )
            else:
                # Map entries are schema names, including default/examples/id.
                pending.append(iter((item, True) for item in value.values()))
        else:
            pending.append(iter((item, True) for item in value))
    return True


def _local_schema_pointer(reference: Any) -> tuple[str, ...] | None:
    """Decode URI fragments before JSON Pointer escapes, rejecting ambiguity."""
    if (
        not isinstance(reference, str)
        or not reference.startswith("#/")
        or _INVALID_PERCENT_ESCAPE.search(reference)
    ):
        return None
    try:
        pointer = unquote(reference[1:], errors="strict")
    except UnicodeDecodeError:
        return None
    if not pointer.startswith("/") or _INVALID_POINTER_ESCAPE.search(pointer):
        return None
    return tuple(
        part.replace("~1", "/").replace("~0", "~") for part in pointer[1:].split("/")
    )


def _schema_annotations(
    value: Any,
    root: Any,
    *,
    sensitive: bool = False,
    references: frozenset[_ReferenceKey] = frozenset(),
    reference_results: dict[_ReferenceKey, bool] | None = None,
    single_resource: bool | None = None,
) -> Any:
    """Separate structural schema names from credential-bearing instance data."""
    if reference_results is None:
        reference_results = {}
    if single_resource is None:
        single_resource = _single_schema_resource(root)
    if isinstance(value, list):
        return [
            _schema_annotations(
                item,
                root,
                sensitive=sensitive,
                references=references,
                reference_results=reference_results,
                single_resource=single_resource,
            )
            for item in value
        ]
    if not isinstance(value, dict):
        return value
    if sensitive and any(key in value for key in _SCHEMA_REFERENCES - {"$ref"}):
        # Dynamic/recursive scope cannot be proved by following a local pointer.
        # Keep the live/private definition intact; omit its public review unit.
        return dict(REDACTED_SECRET_SENTINEL)
    if sensitive and "$ref" in value:
        pointer = _local_schema_pointer(value["$ref"])
        reference = (id(root), pointer) if pointer is not None else None
        target = root
        if (
            not single_resource
            or reference is None
            or reference in references
            or len(references) >= 16
        ):
            return dict(REDACTED_SECRET_SENTINEL)
        if reference not in reference_results:
            try:
                for part in reference[1]:
                    target = target[part]
            except KeyError, TypeError:
                return dict(REDACTED_SECRET_SENTINEL)
            # Memoize proofs per schema: repeated reference fan-out must not
            # expand exponentially. An in-progress proof is conservative.
            reference_results[reference] = True
            reference_results[reference] = _unsafe(
                _schema_annotations(
                    target,
                    root,
                    sensitive=True,
                    references=references | {reference},
                    reference_results=reference_results,
                    single_resource=single_resource,
                )
            )
        if reference_results[reference]:
            return dict(REDACTED_SECRET_SENTINEL)
    result = {}
    for key, item in value.items():
        if key in _SCHEMA_URI_KEYWORDS:
            result[key] = _schema_reference_uri(item)
        elif key == "$vocabulary":
            # Vocabulary names are URIs, not ordinary schema-map labels. Keep
            # the boolean declarations intact, but omit the coherent tool if a
            # URI carries credentials or the map cannot be classified safely.
            result[key] = (
                deepcopy(item)
                if isinstance(item, dict)
                and all(
                    type(required) is bool and _schema_reference_uri(uri) == uri
                    for uri, required in item.items()
                )
                else dict(REDACTED_SECRET_SENTINEL)
            )
        elif key in _SCHEMA_INSTANCE_KEYWORDS:
            # Instance data cannot acquire schema exemptions by using keys such
            # as functions/spec/parameters. No user content is executed here.
            classified = _structured_payloads(redact_secrets(item, schema_roots=False))
            result[key] = (
                dict(REDACTED_SECRET_SENTINEL)
                if sensitive or _unsafe(classified)
                else deepcopy(item)
            )
        elif key in _SCHEMA_MAP_KEYWORDS and isinstance(item, dict):
            result[key] = {
                name: _schema_annotations(
                    definition,
                    root,
                    sensitive=sensitive
                    or (
                        key in {"properties", "patternProperties"}
                        and _is_secret_key(name)
                    ),
                    references=references,
                    reference_results=reference_results,
                    single_resource=single_resource,
                )
                for name, definition in item.items()
            }
        else:
            result[key] = _schema_annotations(
                item,
                root,
                sensitive=sensitive,
                references=references,
                reference_results=reference_results,
                single_resource=single_resource,
            )
    return result


def _structured_payloads(
    value: Any, *, path: tuple[Any, ...] = (), function_schemas: bool = False
) -> Any:
    """Do not put redaction markers inside executable opaque request strings."""
    if isinstance(value, list):
        return [
            _structured_payloads(
                item, path=(*path, index), function_schemas=function_schemas
            )
            for index, item in enumerate(value)
        ]
    if not isinstance(value, dict):
        return value
    if function_schemas and _is_function_tool_schema_root(path):
        return _schema_annotations(value, value)
    result = {}
    for key, item in value.items():
        if key in {"payload", "payload_template"} and isinstance(item, str):
            try:
                structured = json.loads(item)
            except ValueError, RecursionError:
                pass  # Arbitrary reviewed source remains explicitly uncertain.
            else:
                if _unsafe(redact_secrets(structured, schema_roots=False)):
                    result[key] = dict(REDACTED_SECRET_SENTINEL)
                    continue
        result[key] = _structured_payloads(
            item, path=(*path, key), function_schemas=function_schemas
        )
    return result


def project_configuration(
    config: dict[str, Any], *, reviewed: bool = False
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return a valid partial configuration and a value-free omission manifest."""
    from .agent_config import AGENT_CONFIG_FIELDS

    summary = security_summary(reviewed)
    result: dict[str, Any] = {"functions": [], "function_groups": [], "skills": []}
    decoded = decoded_function_fields(config)
    redacted = (
        _structured_payloads(redact_secrets(decoded), function_schemas=True)
        if reviewed
        else {}
    )
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
            candidate = _structured_payloads(
                redact_secrets({"rules": [rule]}, schema_roots=False)
            )["rules"][0]
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
    elif _unsafe(redact_secrets(result, schema_roots=False)):
        # Includes credential-bearing group/wording metadata.
        record_omission(summary, "rule_metadata")
        return project_rules(data, summary, reviewed=False)
    return result
