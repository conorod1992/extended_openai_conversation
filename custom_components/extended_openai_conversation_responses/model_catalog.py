"""Validated descriptive model data; compatibility matching stays in Python."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re
from typing import Any

MAX_CATALOG_BYTES = 256 * 1024
_PARAMETERS = {
    "supports_top_p",
    "supports_temperature",
    "supports_max_tokens",
    "supports_max_completion_tokens",
    "supports_reasoning_effort",
    "supports_service_tier",
}
_FLAGS = {"completion_token_limit", "chat_reasoning_tools", "explicit_prompt_cache"}
_METADATA = {"parameters", "reasoning_efforts", *_FLAGS}
_EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"}
_ID = re.compile(r"[a-z0-9][a-z0-9._:-]{0,127}\Z")


def _keys(value: Any, required: set[str], optional: set[str] | None = None) -> None:
    if (
        not isinstance(value, dict)
        or not required <= value.keys()
        or value.keys() - required - (optional or set())
    ):
        raise ValueError("Unexpected or missing catalogue fields")


def _metadata(value: dict[str, Any]) -> None:
    parameters = value["parameters"]
    _keys(parameters, _PARAMETERS)
    if any(type(item) is not bool for item in parameters.values()):
        raise ValueError("Model parameters must be boolean")
    if any(type(value[key]) is not bool for key in _FLAGS):
        raise ValueError("Model capabilities must be boolean")
    efforts = value["reasoning_efforts"]
    if (
        not isinstance(efforts, list)
        or not efforts
        or len(efforts) > len(_EFFORTS)
        or any(not isinstance(item, str) or item not in _EFFORTS for item in efforts)
        or len(set(efforts)) != len(efforts)
    ):
        raise ValueError("Invalid reasoning efforts")


def validate_catalog(value: Any) -> dict[str, Any]:
    """Reject unknown fields/types and unsupported schemas before any activation."""
    _keys(value, {"schema_version", "catalog_version", "defaults", "models"})
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ValueError("Unsupported model catalogue schema_version")
    if type(value["catalog_version"]) is not int or value["catalog_version"] < 1:
        raise ValueError("catalog_version must be a positive integer")
    _keys(value["defaults"], _METADATA)
    _metadata(value["defaults"])
    models = value["models"]
    if not isinstance(models, list) or not 1 <= len(models) <= 512:
        raise ValueError("Invalid model list")
    ids = set()
    for model in models:
        _keys(
            model,
            {"id", "display_name", "kind", *_METADATA},
            {"deprecated", "replacement"},
        )
        model_id = model["id"]
        if (
            not isinstance(model_id, str)
            or not _ID.fullmatch(model_id)
            or model_id in ids
        ):
            raise ValueError("Invalid or duplicate model ID")
        ids.add(model_id)
        if (
            not isinstance(model["display_name"], str)
            or not 1 <= len(model["display_name"]) <= 128
        ):
            raise ValueError("Invalid display name")
        if model["kind"] not in ("alias", "snapshot"):
            raise ValueError("Invalid model kind")
        if "deprecated" in model and type(model["deprecated"]) is not bool:
            raise ValueError("Invalid deprecation flag")
        replacement = model.get("replacement")
        if replacement is not None and (
            not isinstance(replacement, str)
            or not _ID.fullmatch(replacement)
            or replacement == model_id
        ):
            raise ValueError("Invalid replacement model")
        _metadata(model)
    return deepcopy(value)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def parse_catalog(raw: bytes) -> dict[str, Any]:
    """Bound downloaded data and reject ambiguous JSON before schema validation."""
    if len(raw) > MAX_CATALOG_BYTES:
        raise ValueError("Model catalogue too large")
    return validate_catalog(json.loads(raw, object_pairs_hook=_unique_object))


# Imported with the integration's modules; no network or request-time file I/O.
BUNDLED_CATALOG = parse_catalog(Path(__file__).with_suffix(".json").read_bytes())
_active = BUNDLED_CATALOG


def activate_catalog(catalog: dict[str, Any] | None) -> None:
    """Publish a complete, validated snapshot in a single assignment."""
    global _active
    if catalog is None:
        _active = BUNDLED_CATALOG
        return
    candidate = validate_catalog(catalog)
    # Remote catalogues may describe a subset; omitted models retain bundled data.
    models = {item["id"]: item for item in BUNDLED_CATALOG["models"]}
    models.update({item["id"]: item for item in candidate["models"]})
    _active = {**candidate, "models": list(models.values())}


def all_reasoning_efforts() -> list[str]:
    """Allowed choices when a rule resolves its model only at request time."""
    return list(
        dict.fromkeys(
            effort
            for item in [_active["defaults"], *_active["models"]]
            for effort in item["reasoning_efforts"]
        )
    )


def model_metadata(model: str) -> dict[str, Any]:
    """Exact/snapshot metadata plus the integration's historical family fallback.

    Matching is deliberately code, never a regex/expression supplied by a server.
    Preserve the old broad family handling for unknown and compatible-provider IDs.
    """
    name = model.lower()
    models = {item["id"]: item for item in _active["models"]}
    if name in models:
        return deepcopy(models[name])
    # Only dated snapshots inherit an alias wholesale; arbitrary suffixes retain
    # the legacy compatibility rules below (including their token-limit quirks).
    for alias in sorted(models, key=len, reverse=True):
        if models[alias]["kind"] == "alias" and re.fullmatch(
            re.escape(alias) + r"-\d{4}-\d{2}-\d{2}", name
        ):
            return deepcopy(models[alias])
    result: dict[str, Any] = deepcopy(_active["defaults"])
    family = re.match(r"^(o[1-4]|gpt-5|gpt-6-astra(?:[-.]|$))", name)
    if family:
        key = "gpt-6-astra" if name.startswith("gpt-6-astra") else family[1]
        source = models.get(key, _active["defaults"])
        result["parameters"] = deepcopy(source["parameters"])
        result["reasoning_efforts"] = list(source["reasoning_efforts"])
    token_family = re.search(r"(^|-)(gpt-4o|gpt-5|gpt-6-astra|o1|o3|o4)", name)
    if token_family:
        result["completion_token_limit"] = models[token_family[2]][
            "completion_token_limit"
        ]
    if re.match(r"^gpt-6-astra(?:[-.]|$)", name):
        result["chat_reasoning_tools"] = models["gpt-6-astra"]["chat_reasoning_tools"]
    minor = re.match(r"^gpt-5\.(\d+)(?:[-.]|$)", name)
    if minor and int(minor[1]) >= 6:
        for key in ("chat_reasoning_tools", "explicit_prompt_cache"):
            result[key] = models["gpt-5.6"][key]
    return result
