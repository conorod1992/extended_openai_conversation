"""Structure-preserving credential redaction for export and backup data."""

from __future__ import annotations

import re
from typing import Any

REDACTED_SECRET_SENTINEL = {"__extended_openai_redacted_secret__": True}
LITERAL_TEXT_KEY = "__extended_openai_literal_text__"

_SECRET_KEY_PARTS = frozenset(
    {"password", "passwd", "secret", "token", "authorization"}
)
_SECRET_KEY_FAMILIES = ("apikey", "clientsecret", "accesstoken", "refreshtoken")
_CAMEL_CASE_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_KEY_SEPARATOR = re.compile(r"[^A-Za-z0-9]+")
_LIKELY_SECRET = re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b")
_DROP = object()


def _is_secret_key(key: Any) -> bool:
    """Classify credential-like keys without depending on separator spelling."""
    separated = _CAMEL_CASE_BOUNDARY.sub(" ", str(key))
    parts = tuple(part.casefold() for part in _KEY_SEPARATOR.split(separated) if part)
    if any(part in _SECRET_KEY_PARTS for part in parts):
        return True
    canonical = "".join(parts)
    return any(family in canonical for family in _SECRET_KEY_FAMILIES)


def _sentinel() -> dict[str, bool]:
    """Return an isolated sentinel object suitable for JSON serialization."""
    return dict(REDACTED_SECRET_SENTINEL)


def is_redacted_secret(value: Any) -> bool:
    """Recognize structured markers and whole/embedded legacy text markers."""
    return value == REDACTED_SECRET_SENTINEL or (
        isinstance(value, str) and "[redacted]" in value
    )


def is_literal_text(value: Any) -> bool:
    """Recognize escaped user text, distinct from legacy redaction markers."""
    return (
        isinstance(value, dict)
        and set(value) == {LITERAL_TEXT_KEY}
        and isinstance(value[LITERAL_TEXT_KEY], str)
    )


def _is_function_tool_schema_root(path: tuple[Any, ...]) -> bool:
    """Return whether *path* is a configured Function Tool JSON Schema root."""
    return (
        len(path) >= 4
        and path[-4] == "functions"
        and isinstance(path[-3], int)
        and path[-2] == "spec"
        and path[-1] == "parameters"
    )


def _redact_secrets(value: Any, *, schema: bool, path: tuple[Any, ...]) -> Any:
    """Recursively redact secrets while tracking structural schema context."""
    if value == REDACTED_SECRET_SENTINEL:
        return _sentinel()
    if is_literal_text(value):
        value = value[LITERAL_TEXT_KEY]
    if isinstance(value, list):
        return [
            _redact_secrets(item, schema=schema, path=(*path, index))
            for index, item in enumerate(value)
        ]
    if isinstance(value, str):
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
        child_path = (*path, key)
        child_schema = schema or _is_function_tool_schema_root(child_path)
        if not schema and _is_secret_key(key):
            result[key] = _sentinel()
            continue
        result[key] = _redact_secrets(
            item,
            schema=child_schema,
            path=child_path,
        )
    return result


def redact_secrets(value: Any, *, schema: bool = False) -> Any:
    """Redact credential values without deleting their surrounding keys/structure."""
    return _redact_secrets(value, schema=schema, path=())


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
    restored = _restore_redacted_secrets(value)
    return None if restored is _DROP else restored
