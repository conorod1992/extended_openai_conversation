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

from .const import CONTEXT_TRUNCATE_STRATEGIES, SERVICE_TIER_OPTIONS
from .function_yaml import decoded_function_fields
from .model_catalog import BUNDLED_CATALOG, REASONING_EFFORT_VALUES
from .secret_redaction import (
    REDACTED_SECRET_SENTINEL,
    _is_export_secret_key,
    _is_function_tool_schema_root,
    _uses_credential_appid,
    is_redacted_secret,
    redact_http_url,
    redact_secrets,
    redact_text,
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
# These are schema locations, not a list of fields considered secret-free.
# Everything else (including future extension objects) is ordinary data.
_SCHEMA_CHILD_KEYWORDS = frozenset(
    {
        "additionalProperties",
        "unevaluatedProperties",
        "propertyNames",
        "contains",
        "additionalItems",
        "unevaluatedItems",
        "not",
        "if",
        "then",
        "else",
        "contentSchema",
        "items",
        "prefixItems",
        "allOf",
        "anyOf",
        "oneOf",
    }
)
# These select instance values by unclassified property names. Neither sibling
# declarations nor composed schemas establish that those values are public.
# Apply the sensitive-value policy even when the containing object is public;
# type/shape-only schemas still survive, while literals and unproved refs do not.
_SCHEMA_VALUE_WILDCARDS = frozenset({"additionalProperties", "unevaluatedProperties"})
# At sensitive schema nodes, unknown fields are not presumed annotations. A
# numeric bound or authored regex can encode the same credential as const.
# These closed classifications describe shape/names, never instance values.
_SCHEMA_TYPES = frozenset(
    {"null", "boolean", "object", "array", "number", "integer", "string"}
)
_SCHEMA_SHAPE_COUNTS = frozenset(
    {
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
        "minProperties",
        "maxProperties",
        "minContains",
        "maxContains",
    }
)
_SCHEMA_SHAPE_FLAGS = frozenset(
    {
        "uniqueItems",
        "nullable",
        "readOnly",
        "writeOnly",
        "deprecated",
        "$recursiveAnchor",
    }
)
_SCHEMA_TEXT_ANNOTATIONS = frozenset(
    {"title", "description", "$comment", "$anchor", "$dynamicAnchor"}
)
# Standard format labels are classified vocabulary, not arbitrary authored
# constraints. Custom formats need an explicit policy before sensitive sharing.
_SCHEMA_PUBLIC_FORMATS = frozenset(
    {
        "date-time",
        "date",
        "time",
        "duration",
        "email",
        "idn-email",
        "hostname",
        "idn-hostname",
        "ipv4",
        "ipv6",
        "uuid",
        "uri",
        "uri-reference",
        "iri",
        "iri-reference",
        "uri-template",
        "json-pointer",
        "relative-json-pointer",
        "regex",
        "int32",
        "int64",
        "float",
        "double",
        "byte",
        "binary",
        "password",
    }
)
_INVALID_PERCENT_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})")
_INVALID_POINTER_ESCAPE = re.compile(r"~(?:[^01]|$)")
_LITERAL_PROPERTY_PATTERN = re.compile(r"\^([A-Za-z0-9_-]+)\$")
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
    if isinstance(value, str):
        # Final unit admission is independent of every walker's schema context.
        # This is the same value policy, not a second collection of patterns.
        return is_redacted_secret(redact_text(value, for_export=True))
    if isinstance(value, dict):
        return any(
            not isinstance(key, str)
            or is_redacted_secret(redact_text(key, for_export=True))
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
        if redact_http_url(candidate, for_export=True) is not None
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


def _sensitive_schema_property(
    name: Any, *, pattern: bool, provider_appid: bool = False
) -> bool:
    """Classify literal names; never infer regex meaning from its spelling.

    An anchored literal is a bounded proof of the matched field name. Other
    authored patterns are unclassified, so their instance literals and sensitive
    references require omission. Type/shape-only schemas remain portable without
    executing an authored regex or changing the live/private definition.
    """

    def sensitive(candidate: Any) -> bool:
        return _is_export_secret_key(candidate) or (
            provider_appid and str(candidate).casefold() == "appid"
        )

    if not pattern:
        return sensitive(name)
    literal = (
        _LITERAL_PROPERTY_PATTERN.fullmatch(name) if isinstance(name, str) else None
    )
    return literal is None or sensitive(literal[1])


def _classified_sensitive_schema_field(key: str, value: Any) -> bool:
    """Admit only declared shape/name fields and reviewed standard annotations.

    Structural children, references and instance literals have dedicated paths.
    Never execute a regex, infer equivalent constants or guess custom semantics.
    Annotation prose remains explicitly uncertain reviewed content and still
    passes the shared scalar/credential checks below and at final admission.
    """
    if key == "type":
        types = value if isinstance(value, list) else [value]
        return bool(types) and all(
            isinstance(item, str) and item in _SCHEMA_TYPES for item in types
        )
    if key in _SCHEMA_SHAPE_COUNTS:
        return (type(value) is int and value >= 0) or (
            type(value) is float
            and math.isfinite(value)
            and value >= 0
            and value.is_integer()
        )
    if key in _SCHEMA_SHAPE_FLAGS:
        return type(value) is bool
    if key == "required":
        return isinstance(value, list) and all(isinstance(item, str) for item in value)
    if key in _SCHEMA_TEXT_ANNOTATIONS:
        return isinstance(value, str)
    if key == "format":
        return isinstance(value, str) and value in _SCHEMA_PUBLIC_FORMATS
    return False


def _schema_annotations(
    value: Any,
    root: Any,
    *,
    sensitive: bool = False,
    provider_appid: bool = False,
    schema_node: bool = True,
    references: frozenset[_ReferenceKey] = frozenset(),
    reference_results: dict[_ReferenceKey, bool] | None = None,
    single_resource: bool | None = None,
) -> Any:
    """Grant name exemptions only at explicitly classified schema locations.

    Unclassified fields at sensitive nodes require omission; elsewhere they
    enter ordinary data with designated-key checks. Nested schema-looking
    fields in data cannot reacquire structural privileges.
    URI checks remain defensive in both contexts, without resolving references
    or treating opaque data as schema authority.
    """
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
                provider_appid=provider_appid,
                schema_node=schema_node,
                references=references,
                reference_results=reference_results,
                single_resource=single_resource,
            )
            for item in value
        ]
    if not isinstance(value, dict):
        return redact_text(value, for_export=True) if isinstance(value, str) else value
    if (
        schema_node
        and sensitive
        and any(key in value for key in _SCHEMA_REFERENCES - {"$ref"})
    ):
        # Dynamic/recursive scope cannot be proved by following a local pointer.
        # Keep the live/private definition intact; omit its public review unit.
        return dict(REDACTED_SECRET_SENTINEL)
    if schema_node and sensitive and "$ref" in value:
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
                    provider_appid=provider_appid,
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
        elif not schema_node:
            result[key] = _schema_annotations(
                item,
                root,
                schema_node=False,
                provider_appid=provider_appid,
                reference_results=reference_results,
                single_resource=single_resource,
            )
        elif key in _SCHEMA_INSTANCE_KEYWORDS:
            # Instance data cannot acquire schema exemptions by using keys such
            # as functions/spec/parameters. No user content is executed here.
            classified = _structured_payloads(
                redact_secrets(
                    item,
                    schema_roots=False,
                    for_export=True,
                    provider_appid=provider_appid,
                ),
                provider_appid=provider_appid,
            )
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
                        and _sensitive_schema_property(
                            name,
                            pattern=key == "patternProperties",
                            provider_appid=provider_appid,
                        )
                    ),
                    references=references,
                    provider_appid=provider_appid,
                    reference_results=reference_results,
                    single_resource=single_resource,
                )
                for name, definition in item.items()
            }
        elif key == "dependentRequired":
            # Both keys and list members are property names, not instance data
            # or schema keywords (a property named $id remains a name).
            result[key] = (
                deepcopy(item)
                if isinstance(item, dict)
                and all(
                    isinstance(name, str)
                    and isinstance(names, list)
                    and all(isinstance(member, str) for member in names)
                    for name, names in item.items()
                )
                else dict(REDACTED_SECRET_SENTINEL)
            )
        elif key in _SCHEMA_CHILD_KEYWORDS:
            result[key] = _schema_annotations(
                item,
                root,
                sensitive=sensitive or key in _SCHEMA_VALUE_WILDCARDS,
                provider_appid=provider_appid,
                references=references,
                reference_results=reference_results,
                single_resource=single_resource,
            )
        elif sensitive and not _classified_sensitive_schema_field(key, item):
            # A schema position is not proof that a constraint carries no
            # credential. Omit the whole reviewed unit, preserving its source
            # for live execution and private restoration.
            result[key] = dict(REDACTED_SECRET_SENTINEL)
        else:
            # Include the annotation's own key: api_key at a schema node is
            # not the structural property declaration properties.api_key.
            data = _structured_payloads(
                redact_secrets(
                    {key: item},
                    schema_roots=False,
                    for_export=True,
                    provider_appid=provider_appid,
                ),
                provider_appid=provider_appid,
            )
            result[key] = _schema_annotations(
                data[key],
                root,
                schema_node=False,
                provider_appid=provider_appid,
                reference_results=reference_results,
                single_resource=single_resource,
            )
    return result


def _function_uses_credential_appid(value: Any) -> bool:
    """Inspect executable REST nodes, without interpreting Script data as tools."""
    pending = [value]
    seen: set[int] = set()
    while pending:
        node = pending.pop()
        if not isinstance(node, dict) or id(node) in seen:
            continue
        seen.add(id(node))
        if node.get("type") in ("rest", "scrape"):
            if _uses_credential_appid(node.get("resource")):
                return True
        elif node.get("type") == "composite" and isinstance(node.get("sequence"), list):
            pending.extend(node["sequence"])
    return False


def _structured_payloads(
    value: Any,
    *,
    path: tuple[Any, ...] = (),
    function_schemas: bool = False,
    provider_appid: bool = False,
) -> Any:
    """Do not put redaction markers inside executable opaque request strings."""
    if isinstance(value, list):
        return [
            _structured_payloads(
                item,
                path=(*path, index),
                function_schemas=function_schemas,
                provider_appid=provider_appid,
            )
            for index, item in enumerate(value)
        ]
    if not isinstance(value, dict):
        return value
    tool_node = function_schemas and len(path) == 2 and path[0] == "functions"
    if tool_node:
        # The provider lives beside spec, not above it. Scope the evidence to
        # this coherent tool, including any executable Composite children.
        provider_appid = _function_uses_credential_appid(value.get("function"))
    if function_schemas and _is_function_tool_schema_root(path):
        return _schema_annotations(value, value, provider_appid=provider_appid)
    provider_appid = provider_appid or (
        value.get("type") in ("rest", "scrape")
        and _uses_credential_appid(value.get("resource"))
    )
    result = {}
    for key, item in value.items():
        if key in {"payload", "payload_template"} and isinstance(item, str):
            try:
                structured = json.loads(item)
            except ValueError, RecursionError:
                pass  # Arbitrary reviewed source remains explicitly uncertain.
            else:
                if _unsafe(
                    redact_secrets(
                        structured,
                        schema_roots=False,
                        for_export=True,
                        provider_appid=provider_appid,
                    )
                ):
                    result[key] = dict(REDACTED_SECRET_SENTINEL)
                    continue
        result[key] = _structured_payloads(
            item,
            path=(*path, key),
            function_schemas=function_schemas,
            provider_appid=False if tool_node and key == "function" else provider_appid,
        )
    return result


def _reviewed_content(value: Any, *, configuration: bool = False) -> Any:
    """One pipeline for reviewed configuration, Rule bodies and Rule metadata.

    Stages: credential values/keys, schema semantics and structured request
    payloads, then coherent-unit admission by _unsafe. No renderer is invoked.
    Configuration is the only context that owns Function parameter schemas.
    """
    return _structured_payloads(
        redact_secrets(value, schema_roots=configuration, for_export=True),
        function_schemas=configuration,
    )


def project_sharing_sections(
    sections: dict[str, Any], *, reviewed: bool = False
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Sole public-writer boundary; new sections require explicit classification.

    Private/legacy writers deliberately do not use this lossy projection.
    Writers must build their envelopes from these sections and public metadata,
    without merging source fields back into the artifact afterward.
    """
    projected: dict[str, Any] = {}
    summary = security_summary(reviewed)
    if "configuration" in sections:
        projected["configuration"], summary = project_configuration(
            sections["configuration"], reviewed=reviewed
        )
    if "request_rules" in sections:
        projected["request_rules"] = project_rules(
            sections["request_rules"], summary, reviewed=reviewed
        )
    if sections.keys() - {"configuration", "request_rules"}:
        record_omission(summary, "unclassified_section")
    return projected, summary


def _function_name(tool: Any) -> str | None:
    spec = tool.get("spec") if isinstance(tool, dict) else None
    name = spec.get("name") if isinstance(spec, dict) else None
    return name if isinstance(name, str) else None


def _group_members(group: Any) -> list[str]:
    members = group.get("functions", []) if isinstance(group, dict) else []
    return (
        [name for name in members if isinstance(name, str)]
        if isinstance(members, list)
        else []
    )


def _project_reviewed_groups(
    groups: Any,
    candidates: Any,
    tools: Any,
    retained: list[Any],
    summary: dict[str, Any],
) -> Any:
    if not isinstance(groups, list):
        # Recovery exports may carry an opaque quarantined group collection.
        if not _unsafe(candidates):
            return deepcopy(candidates)
        record_omission(summary, "function_groups")
        return []
    source_names = (
        {_function_name(tool) for tool in tools} if isinstance(tools, list) else set()
    )
    retained_names = {_function_name(tool) for tool in retained}
    omitted_names = source_names - retained_names - {None}
    projected = []
    blocked_members: set[str] = set()
    for index, group in enumerate(groups):
        present = isinstance(candidates, list) and index < len(candidates)
        candidate = candidates[index] if present else None
        if not present or _unsafe(candidate):
            # Losing metadata must not promote disabled/on-demand members.
            blocked_members.update(_group_members(group))
            record_omission(summary, f"function_group_{index + 1}")
            continue
        if (
            not isinstance(candidate, dict)
            or not isinstance(candidate.get("functions", []), list)
            or any(not isinstance(name, str) for name in candidate.get("functions", []))
        ):
            # Keep safe repair-owned records opaque, rather than fixing their
            # shape (and potentially activating them) during an export.
            projected.append(deepcopy(candidate))
            continue
        # Remove only references to tools actually omitted by sharing. Unknown
        # references belong to quarantine and must not be silently repaired.
        members = [
            name for name in candidate.get("functions", []) if name not in omitted_names
        ]
        if members or not candidate.get("functions"):
            projected.append({**deepcopy(candidate), "functions": members})
        else:
            record_omission(summary, f"function_group_{index + 1}")
    for tool in retained:
        if _function_name(tool) in blocked_members:
            tool["enabled"] = False
            record_omission(summary, "function_group_member_disabled")
    return projected


def project_configuration(
    config: dict[str, Any], *, reviewed: bool = False
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return a valid partial configuration and a value-free omission manifest."""
    from .agent_config import AGENT_CONFIG_FIELDS

    summary = security_summary(reviewed)
    result: dict[str, Any] = {"functions": [], "function_groups": [], "skills": []}
    decoded = decoded_function_fields(config)
    redacted = _reviewed_content(decoded, configuration=True) if reviewed else {}
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
        elif key == "function_groups":
            if not reviewed:
                record_omission(summary, key)
            continue  # Project coherent groups after their retained members are known.
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
            present = isinstance(candidates, list) and index < len(candidates)
            candidate = candidates[index] if present else None
            if reviewed and present and not _unsafe(candidate):
                result["functions"].append(deepcopy(candidate))
            else:
                record_omission(summary, f"function_tool_{index + 1}")
    elif tools:
        record_omission(summary, "function_tool")
    if reviewed:
        if isinstance(tools, list) and len(result["functions"]) != len(tools):
            from .function_configuration_repair import (
                function_configuration_repair_state,
            )

            repair = function_configuration_repair_state(decoded)
            if repair is not None and not repair.field_updates_allowed:
                # Omitting one duplicate/ambiguous definition can make another
                # executable. Preserve the source's fail-closed collection state.
                for tool in result["functions"]:
                    if _function_name(tool) is not None:
                        tool["enabled"] = False
                        record_omission(summary, "function_collection_member_disabled")
        result["function_groups"] = _project_reviewed_groups(
            decoded.get("function_groups", []),
            redacted.get("function_groups", []),
            tools,
            result["functions"],
            summary,
        )
        if result.get("skills"):
            from .function_configuration_repair import (
                function_configuration_repair_state,
            )
            from .skill_availability import skill_loader_status

            # Sharing can remove or disable a loader after source validation.
            # Preserve quarantine, but do not export dangling Skill selections
            # that the ordinary configuration importer cannot accept.
            repair = function_configuration_repair_state(result)
            tools_for_skills = repair.valid if repair else result["functions"]
            groups_for_skills = repair.groups if repair else result["function_groups"]
            if not skill_loader_status(
                result["skills"],
                tools_for_skills,
                groups_for_skills,
                max_function_calls=result.get("max_function_calls_per_conversation"),
            ).available:
                record_omission(summary, "skills", count=len(result["skills"]))
                result["skills"] = []
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
            candidate = _reviewed_content({"rules": [rule]})["rules"][0]
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
    elif _unsafe(_reviewed_content(result)):
        # Includes credential-bearing group/wording metadata.
        record_omission(summary, "rule_metadata")
        return project_rules(data, summary, reviewed=False)
    return result
