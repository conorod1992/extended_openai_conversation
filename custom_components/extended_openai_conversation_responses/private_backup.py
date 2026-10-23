"""Versioned lossless private fields; never interpret user data as redaction."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

import yaml

from .bounded_json import check_redaction_budget, check_utf8_budget

PRIVATE_BACKUP_VERSION = 6
PRIVATE_TRANSFER_VERSION = 3
_ENCODING = "yaml-safe-v1"
_LIMIT = 128 * 1024 * 1024


class DecodedPrivateDocument(dict[str, Any]):
    """Internal provenance, never a user-controlled wire marker."""


def encode_field(value: Any) -> dict[str, Any]:
    check_redaction_budget(value, _LIMIT)
    # Escape Unicode line separators: YAML otherwise folds NEL into spaces.
    content = yaml.safe_dump(value, sort_keys=False, allow_unicode=False)
    check_utf8_budget(content, _LIMIT)
    return {"encoding": _ENCODING, "content": content}


def decode_field(value: Any) -> Any:
    if not isinstance(value, dict) or set(value) != {"encoding", "content"}:
        raise ValueError("Invalid private field encoding")
    if value["encoding"] != _ENCODING or not isinstance(value["content"], str):
        raise ValueError("Unsupported private field encoding")
    check_utf8_budget(value["content"], _LIMIT)
    decoded = yaml.safe_load(value["content"])
    check_redaction_budget(decoded, _LIMIT)
    return decoded


def is_private_document(value: Any) -> bool:
    return isinstance(value, dict) and (
        (
            value.get("format") == "extended_openai_conversation_backup"
            and value.get("version") == PRIVATE_BACKUP_VERSION
        )
        or (
            value.get("format") == "extended_openai_conversation_transfer"
            and value.get("version") == PRIVATE_TRANSFER_VERSION
            and value.get("mode") == "custom"
        )
    )


def encode_document(snapshot: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(snapshot)
    if result["format"] == "extended_openai_conversation_backup":
        result["agent"]["config"] = encode_field(result["agent"]["config"])
        result["request_rules"] = encode_field(result["request_rules"])
    else:
        for key in {"configuration", "request_rules"} & result["sections"].keys():
            result["sections"][key] = encode_field(result["sections"][key])
    if "provider_connection" in result:
        result["provider_connection"] = encode_field(result["provider_connection"])
    return result


def decode_document(document: Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(document, DecodedPrivateDocument):
        return document
    result = DecodedPrivateDocument(deepcopy(dict(document)))
    if result["format"] == "extended_openai_conversation_backup":
        result["agent"]["config"] = decode_field(result["agent"]["config"])
        result["request_rules"] = decode_field(result["request_rules"])
    else:
        for key in {"configuration", "request_rules"} & result["sections"].keys():
            result["sections"][key] = decode_field(result["sections"][key])
    if "provider_connection" in result:
        result["provider_connection"] = decode_field(result["provider_connection"])
        if not isinstance(result["provider_connection"], dict):
            raise ValueError("Invalid private provider connection")
    return result
