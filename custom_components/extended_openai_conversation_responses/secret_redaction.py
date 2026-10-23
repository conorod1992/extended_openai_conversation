"""Structure-preserving credential redaction for export and backup data."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import unquote_plus, urlsplit, urlunsplit

from .function_yaml import decoded_function_fields, json_safe_function_fields
from .yaml_serialization import dump_yaml

REDACTED_SECRET_SENTINEL = {"__extended_openai_redacted_secret__": True}
REDACTED_URL_KEY = "__extended_openai_redacted_url__"
LITERAL_TEXT_KEY = "__extended_openai_literal_text__"

_SECRET_KEY_PARTS = frozenset(
    {"password", "passwd", "secret", "token", "authorization"}
)
_SECRET_KEY_FAMILIES = ("apikey", "clientsecret", "accesstoken", "refreshtoken")
_CAMEL_CASE_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_KEY_SEPARATOR = re.compile(r"[^A-Za-z0-9]+")
_LIKELY_SECRET = re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b")
_HTTP_CREDENTIAL_NAMES = frozenset(
    {"key", "sig", "signature", "credential", "x-amz-signature", "x-amz-credential"}
)
_JINJA_DELIMITERS = ("{{", "{%", "{#")
_DROP = object()


def _is_secret_key(key: Any) -> bool:
    """Classify credential-like keys without depending on separator spelling."""
    separated = _CAMEL_CASE_BOUNDARY.sub(" ", str(key))
    parts = tuple(part.casefold() for part in _KEY_SEPARATOR.split(separated) if part)
    # Named metadata fields are configuration, even when they describe a
    # credential. Keep conservative matching for other credential-like names,
    # including variants such as authorization_header and clientSecretValue.
    if len(parts) > 1 and parts[-1] in {
        "count",
        "counts",
        "name",
        "names",
        "enabled",
        "disabled",
        "limit",
        "limits",
        "length",
        "size",
        "usage",
        "budget",
    }:
        return False
    if any(part in _SECRET_KEY_PARTS for part in parts):
        return True
    canonical = "".join(parts)
    return any(family in canonical for family in _SECRET_KEY_FAMILIES)


def _is_http_credential_key(key: Any) -> bool:
    """Use one credential policy for HTTP query strings and request params."""
    name = unquote_plus(str(key))
    return _is_secret_key(name) or name.casefold() in _HTTP_CREDENTIAL_NAMES


def _is_payload_secret_key(key: Any) -> bool:
    """Distinguish credentials from user-defined Script variable/data names."""
    separated = _CAMEL_CASE_BOUNDARY.sub(" ", str(key))
    parts = tuple(part.casefold() for part in _KEY_SEPARATOR.split(separated) if part)
    if not _is_secret_key(key):
        return False
    # Arbitrary payload names such as token_index and secret_reference are not
    # credential settings. Still redact credential names and common wrappers.
    if parts and parts[-1] in {"value", "header"}:
        parts = parts[:-1]
    return bool(parts) and (
        parts[-1] in _SECRET_KEY_PARTS or "".join(parts).endswith(_SECRET_KEY_FAMILIES)
    )


def is_redacted_url(value: Any) -> bool:
    """Recognize an HTTP credential marker with its visible destination binding."""
    return (
        isinstance(value, dict)
        and set(value) == {REDACTED_URL_KEY}
        and (
            value[REDACTED_URL_KEY] is None or isinstance(value[REDACTED_URL_KEY], str)
        )
    )


def _is_literal_http_url(value: str) -> bool:
    """Use URL-parser normalization without guessing from a field's name.

    Inspect only the scheme so a malformed authority still reaches credential
    redaction, while ordinary prose, scripts and non-URL templates remain data.
    The parser handles leading C0/space and embedded tab/newline characters.
    """
    scheme_end = value.find(":")
    if scheme_end < 0:
        return False
    try:
        return urlsplit(value[: scheme_end + 1]).scheme.lower() in {"http", "https"}
    except ValueError:
        return False


def _redact_url_parameters(value: str) -> tuple[str, bool]:
    """Apply one credential policy to query and OAuth fragment parameters."""
    components = []
    changed = False
    for component in value.split("&"):
        name, separator, content = component.partition("=")
        if separator and (
            _is_http_credential_key(name)
            or _LIKELY_SECRET.search(unquote_plus(content))
        ):
            content = "[redacted]"
            changed = True
        components.append(name + separator + content)
    return "&".join(components), changed


def redact_http_url(value: str, *, require_http: bool = False) -> dict[str, Any] | None:
    """Hide HTTP user information/parameter credentials, retaining public URL bytes.

    The marker is a scalar replacement, not a runnable URL. Recovery compares
    the entire public projection, including non-secret query values and order.
    Unsupported malformed URLs cannot authorize recovery of a local credential.
    """
    if any(delimiter in value for delimiter in _JINJA_DELIMITERS):
        # Never execute templates to export them, or let a hidden destination
        # authorize recovery of local credentials, even for an identical source.
        return {REDACTED_URL_KEY: None}
    if require_http and not value.lower().startswith(("http://", "https://")):
        # Template fields retain the conservative literal-source restriction:
        # hide ambiguous input without authorizing local credential recovery.
        return {REDACTED_URL_KEY: None}
    try:
        # Like the HTTP client's URL parser, urlsplit removes leading C0/space
        # and embedded tab/newline characters before interpreting the scheme.
        parsed = urlsplit(value)
    except ValueError:
        return {REDACTED_URL_KEY: None}
    if parsed.scheme.lower() not in {"http", "https"}:
        return {REDACTED_URL_KEY: None} if require_http else None
    authority = parsed.netloc
    changed = "@" in authority
    if changed:
        authority = "%5Bredacted%5D@" + authority.rsplit("@", 1)[1]
    query, query_changed = _redact_url_parameters(parsed.query)
    fragment, fragment_changed = _redact_url_parameters(parsed.fragment)
    if not (changed or query_changed or fragment_changed):
        return None
    public = urlunsplit((parsed.scheme, authority, parsed.path, query, fragment))
    # Do not emit a second credential detected outside the supported URL parts.
    return {REDACTED_URL_KEY: None if _LIKELY_SECRET.search(public) else public}


def _sentinel() -> dict[str, bool]:
    """Return an isolated sentinel object suitable for JSON serialization."""
    return dict(REDACTED_SECRET_SENTINEL)


def is_redacted_secret(value: Any) -> bool:
    """Recognize structured markers and whole/embedded legacy text markers."""
    return (
        is_redacted_url(value)
        or value == REDACTED_SECRET_SENTINEL
        or (isinstance(value, str) and "[redacted]" in value)
    )


def is_literal_text(value: Any) -> bool:
    """Recognize escaped user text, distinct from legacy redaction markers."""
    return (
        isinstance(value, dict)
        and set(value) == {LITERAL_TEXT_KEY}
        and isinstance(value[LITERAL_TEXT_KEY], str)
    )


def _is_function_tool_schema_root(path: tuple[Any, ...]) -> bool:
    """Recognize schemas only relative to a caller-owned configuration root.

    Export writers extract configuration before calling us. A matching suffix
    inside Script variables or service data does not make that data a schema.
    """
    return (
        len(path) == 4
        and path[0] == "functions"
        and isinstance(path[1], int)
        and path[2] == "spec"
        and path[3] == "parameters"
    )


def _redact_secrets(
    value: Any,
    *,
    schema: bool,
    path: tuple[Any, ...],
    action: bool = False,
    payload: bool = False,
    rule_action: bool = False,
    http_params: bool = False,
    schema_roots: bool = True,
) -> Any:
    """Recursively redact secrets while tracking structural schema context."""
    if is_redacted_url(value):
        # Marker-shaped persisted input is still untrusted on export. Normalize
        # its visible URL too, rather than allowing the wrapper to hide a secret.
        public = value[REDACTED_URL_KEY]
        return (redact_http_url(public) if isinstance(public, str) else None) or {
            REDACTED_URL_KEY: None
        }
    if value == REDACTED_SECRET_SENTINEL:
        return _sentinel()
    if is_literal_text(value):
        value = value[LITERAL_TEXT_KEY]
    if isinstance(value, set):
        # YAML sets contain hashable scalar keys, so a structured sentinel
        # cannot replace one member. Redact the whole leaf for same-agent
        # recovery, just as strings containing an embedded credential are.
        if any(isinstance(item, str) and _LIKELY_SECRET.search(item) for item in value):
            return _sentinel()
        return value.copy()
    if isinstance(value, list):
        return [
            _redact_secrets(
                item,
                schema=schema,
                path=(*path, index),
                action=action,
                payload=payload,
                http_params=http_params,
                schema_roots=schema_roots,
            )
            for index, item in enumerate(value)
        ]
    if isinstance(value, str):
        if not schema and (
            (path and path[-1] in {"resource", "url", "resource_template"})
            or _is_literal_http_url(value)
        ):
            redacted_url = redact_http_url(
                value, require_http=bool(path and path[-1] == "resource_template")
            )
            if redacted_url is not None:
                return redacted_url
        # The entire leaf must be recoverable. Substring replacement loses the
        # credential while leaving a seemingly usable, permanently corrupt value.
        if _LIKELY_SECRET.search(value):
            return _sentinel()
        # New exports must distinguish literal prose from ambiguous old markers.
        return {LITERAL_TEXT_KEY: value} if "[redacted]" in value else value
    if not isinstance(value, dict):
        return value
    result: dict[Any, Any] = {}
    for key, item in value.items():
        if rule_action and key == "canonical_signature":
            # This derived field encodes the unredacted action as JSON. Import
            # rebuilds it from actions; exporting it would duplicate credentials.
            continue
        child_path = (*path, key)
        child_schema = schema or (
            schema_roots and _is_function_tool_schema_root(child_path)
        )
        secret_key = _is_payload_secret_key(key) if payload else _is_secret_key(key)
        http_cookie = (
            path
            and isinstance(path[-1], str)
            and path[-1].casefold() == "headers"
            and str(key).casefold() in {"cookie", "set-cookie"}
        )
        if not schema and (
            secret_key or http_cookie or (http_params and _is_http_credential_key(key))
        ):
            result[key] = _sentinel()
            continue
        result[key] = _redact_secrets(
            item,
            schema=child_schema,
            path=child_path,
            action=(
                not payload
                and (
                    (
                        action
                        and key
                        in {
                            "sequence",
                            "then",
                            "else",
                            "default",
                            "parallel",
                            "repeat",
                            "choose",
                        }
                    )
                    or (value.get("type") == "script" and key == "sequence")
                    or (rule_action and key == "actions")
                )
            ),
            payload=payload
            or (action and key in {"variables", "data", "service_data"})
            or (value.get("type") == "script" and key == "variables"),
            rule_action=value.get("action_type") == "local_action" and key == "action",
            http_params=(
                not schema
                and not payload
                and value.get("type") in ("rest", "scrape")
                and key == "params"
            ),
            schema_roots=schema_roots,
        )
    return result


def redact_secrets(
    value: Any, *, schema: bool = False, schema_roots: bool = True
) -> Any:
    """Redact credential values without deleting their surrounding keys/structure."""
    redacted = _redact_secrets(
        decoded_function_fields(value) if schema_roots else value,
        schema=schema,
        path=(),
        schema_roots=schema_roots,
    )
    return (
        json_safe_function_fields(redacted)
        if schema_roots and isinstance(redacted, dict)
        else redacted
    )


def _restore_redacted_secrets(value: Any) -> Any:
    """Remove unresolved markers, including strings from older backups."""
    if is_literal_text(value):
        return value[LITERAL_TEXT_KEY]
    if is_redacted_secret(value):
        return _DROP
    if isinstance(value, list):
        restored_items = []
        for item in value:
            restored = _restore_redacted_secrets(item)
            if restored is not _DROP:
                restored_items.append(restored)
        return restored_items
    if not isinstance(value, dict):
        return value
    restored_mapping: dict[Any, Any] = {}
    for key, item in value.items():
        restored = _restore_redacted_secrets(item)
        if restored is not _DROP:
            restored_mapping[key] = restored
    return restored_mapping


def restore_redacted_secrets(value: Any) -> Any:
    """Remove redaction sentinels before imported/restored data reaches runtime."""
    decoded = decoded_function_fields(value)
    restored = _restore_redacted_secrets(decoded)
    if decoded is not value and isinstance(restored, dict) and "functions" in restored:
        # Preserve untouched quarantined YAML verbatim. If markers were removed,
        # retain its persistence shape while exposing the updated structure.
        restored["functions"] = (
            value["functions"]
            if restored["functions"] == decoded["functions"]
            else dump_yaml(restored["functions"], sort_keys=False)
        )
    return None if restored is _DROP else restored
