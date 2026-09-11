"""Model capability catalogue v2 and conservative compatibility helpers."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re
from typing import Any, cast

MAX_CATALOG_BYTES = 256 * 1024
_ID = re.compile(r"[a-z0-9][a-z0-9._:-]{0,127}\Z")
_EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh", "max"}
_SUPPORT = {"always", "conditional", "never", "undocumented"}
_SEND_POLICIES = {"omit", "omit_unless_configured"}
_STATUSES = {"current", "deprecated", "unknown"}
_APIS = {"responses", "chat_completions"}
_METADATA_REQUIRED = {
    "status",
    "api",
    "function_calling",
    "reasoning",
    "temperature",
    "top_p",
    "limits",
    "streaming",
    "output_tokens",
    "recommended_profile",
    "service_tier",
    "explicit_prompt_cache",
}
_MODEL_WRAPPER_KEYS = {"id", "display_name", "kind"}
_METADATA_OPTIONAL = {"alias_of", "lifecycle_note", "auto_api"}


def _keys(value: Any, required: set[str], optional: set[str] | None = None) -> None:
    if (
        not isinstance(value, dict)
        or not required <= value.keys()
        or value.keys() - required - (optional or set())
    ):
        raise ValueError("Unexpected or missing catalogue fields")


def _bool_map(value: Any, keys: set[str], label: str) -> None:
    _keys(value, keys)
    if any(type(value[key]) is not bool for key in keys):
        raise ValueError(f"{label} values must be boolean")


def _validate_sampling(value: Any, efforts: list[str], label: str) -> None:
    _keys(value, {"support", "allowed_reasoning_efforts", "send_policy"})
    support = value["support"]
    allowed = value["allowed_reasoning_efforts"]
    if support not in _SUPPORT or value["send_policy"] not in _SEND_POLICIES:
        raise ValueError(f"Invalid {label} capability")
    if support == "conditional":
        if (
            not isinstance(allowed, list)
            or not allowed
            or len(set(allowed)) != len(allowed)
            or any(item not in efforts for item in allowed)
        ):
            raise ValueError(f"Invalid conditional {label} reasoning efforts")
    elif allowed is not None:
        raise ValueError(f"{label} allowed_reasoning_efforts must be null")
    if support in {"never", "undocumented"} and value["send_policy"] != "omit":
        raise ValueError(f"{label} must be omitted when unsupported or undocumented")


def _validate_metadata(value: dict[str, Any], *, model_entry: bool = False) -> None:
    optional = set(_METADATA_OPTIONAL)
    if model_entry:
        optional |= _MODEL_WRAPPER_KEYS
    _keys(value, _METADATA_REQUIRED, optional)
    if value["status"] not in _STATUSES:
        raise ValueError("Invalid model lifecycle status")

    _bool_map(
        value["api"],
        {"responses", "chat_completions", "completions_legacy"},
        "API capability",
    )
    auto_api = value.get("auto_api")
    if auto_api is not None and (auto_api not in _APIS or not value["api"][auto_api]):
        raise ValueError("Invalid Auto API preference")

    functions = value["function_calling"]
    _keys(functions, {"responses", "chat_completions", "preferred_api"})
    if (
        type(functions["responses"]) is not bool
        or type(functions["chat_completions"]) is not bool
    ):
        raise ValueError("Function-calling values must be boolean")
    if (
        functions["preferred_api"] not in _APIS
        or not value["api"][functions["preferred_api"]]
    ):
        raise ValueError("Invalid preferred function-calling API")

    reasoning = value["reasoning"]
    _keys(reasoning, {"supported", "efforts", "openai_default"})
    efforts = reasoning["efforts"]
    if type(reasoning["supported"]) is not bool or not isinstance(efforts, list):
        raise ValueError("Invalid reasoning capability")
    if (
        len(efforts) > len(_EFFORTS)
        or len(set(efforts)) != len(efforts)
        or any(not isinstance(item, str) or item not in _EFFORTS for item in efforts)
        or reasoning["supported"] != bool(efforts)
    ):
        raise ValueError("Invalid reasoning efforts")
    if (
        reasoning["openai_default"] is not None
        and reasoning["openai_default"] not in efforts
    ):
        raise ValueError("Invalid OpenAI reasoning default")

    _validate_sampling(value["temperature"], efforts, "temperature")
    _validate_sampling(value["top_p"], efforts, "top_p")

    limits = value["limits"]
    _keys(limits, {"context_tokens", "max_output_tokens"})
    if any(type(limits[key]) is not int or limits[key] <= 0 for key in limits):
        raise ValueError("Model token limits must be positive integers")
    if type(value["streaming"]) is not bool:
        raise ValueError("Streaming capability must be boolean")

    output_tokens = value["output_tokens"]
    _keys(output_tokens, {"responses", "chat_completions", "legacy_max_tokens"})
    if output_tokens != {
        "responses": "max_output_tokens",
        "chat_completions": "max_completion_tokens",
        "legacy_max_tokens": "never_send",
    }:
        raise ValueError("Invalid output-token parameter mapping")

    profile = value["recommended_profile"]
    _keys(profile, {"api", "reasoning_effort", "temperature", "top_p"})
    if profile["api"] not in _APIS or not value["api"][profile["api"]]:
        raise ValueError("Invalid recommended API")
    if (
        profile["reasoning_effort"] is not None
        and profile["reasoning_effort"] not in efforts
    ):
        raise ValueError("Invalid recommended reasoning effort")
    if profile["temperature"] != "omit" or profile["top_p"] != "omit":
        raise ValueError("Recommended sampling profile must omit temperature/top_p")
    if (
        type(value["service_tier"]) is not bool
        or type(value["explicit_prompt_cache"]) is not bool
    ):
        raise ValueError("Compatibility feature flags must be boolean")

    alias_of = value.get("alias_of")
    if alias_of is not None and (
        not isinstance(alias_of, str) or not _ID.fullmatch(alias_of)
    ):
        raise ValueError("Invalid alias target")
    note = value.get("lifecycle_note")
    if note is not None and (not isinstance(note, str) or len(note) > 512):
        raise ValueError("Invalid lifecycle note")


def validate_catalog(value: Any) -> dict[str, Any]:
    """Validate one strict model-capability catalogue v2 document."""
    _keys(value, {"schema_version", "catalog_version", "defaults", "models"})
    if (
        value.get("schema_version") != 2
        or type(value.get("catalog_version")) is not int
    ):
        raise ValueError("Unsupported model catalogue schema")
    if value["catalog_version"] < 2:
        raise ValueError("catalog_version must be at least 2")
    _validate_metadata(value["defaults"])
    if value["defaults"]["status"] != "unknown":
        raise ValueError("Catalogue defaults must describe unknown models")

    models = value["models"]
    if not isinstance(models, list) or not 1 <= len(models) <= 512:
        raise ValueError("Invalid model list")
    ids: set[str] = set()
    for model in models:
        _keys(model, _MODEL_WRAPPER_KEYS | _METADATA_REQUIRED, _METADATA_OPTIONAL)
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
        if model["kind"] not in {"alias", "snapshot"}:
            raise ValueError("Invalid model kind")
        _validate_metadata(model, model_entry=True)
    for model in models:
        alias_of = model.get("alias_of")
        if alias_of is not None and (alias_of == model["id"] or alias_of not in ids):
            raise ValueError("Alias target must reference another catalogue model")
    return deepcopy(value)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def parse_catalog(raw: bytes) -> dict[str, Any]:
    if len(raw) > MAX_CATALOG_BYTES:
        raise ValueError("Model catalogue too large")
    return validate_catalog(json.loads(raw, object_pairs_hook=_unique_object))


BUNDLED_CATALOG = parse_catalog(Path(__file__).with_suffix(".json").read_bytes())
_active = BUNDLED_CATALOG


def migrate_catalog_v1(value: Any) -> dict[str, Any]:
    """Replace ambiguous v1 booleans with authoritative bundled v2 metadata."""
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError("Not a model catalogue v1 document")
    migrated = deepcopy(BUNDLED_CATALOG)
    old_version = value.get("catalog_version")
    if type(old_version) is int:
        migrated["catalog_version"] = max(migrated["catalog_version"], old_version + 1)
    return migrated


def validate_or_migrate_catalog(value: Any) -> tuple[dict[str, Any], bool]:
    if isinstance(value, dict) and value.get("schema_version") == 1:
        return migrate_catalog_v1(value), True
    return validate_catalog(value), False


def _effective_catalog(catalog: dict[str, Any] | None) -> dict[str, Any]:
    if catalog is None:
        return BUNDLED_CATALOG
    catalog = validate_catalog(catalog)
    models = {item["id"]: deepcopy(item) for item in BUNDLED_CATALOG["models"]}
    models.update({item["id"]: deepcopy(item) for item in catalog["models"]})
    return {**deepcopy(catalog), "models": list(models.values())}


def _model_metadata_from(catalog: dict[str, Any], model: str) -> dict[str, Any]:
    model_id = str(model or "").lower()
    for item in catalog["models"]:
        if item["id"] == model_id:
            return cast(dict[str, Any], deepcopy(item))
    result = cast(dict[str, Any], deepcopy(catalog["defaults"]))
    result.update({"id": model_id, "display_name": model_id, "kind": "custom"})
    return result


def catalog_model_metadata(
    catalog: dict[str, Any] | None, model: str
) -> dict[str, Any]:
    return _model_metadata_from(_effective_catalog(catalog), model)


def catalog_reasoning_efforts(catalog: dict[str, Any] | None) -> list[str]:
    effective = _effective_catalog(catalog)
    return list(
        dict.fromkeys(
            e for item in effective["models"] for e in item["reasoning"]["efforts"]
        )
    )


def catalog_picker_models(
    catalog: dict[str, Any] | None = None, selected_model: str | None = None
) -> list[dict[str, Any]]:
    """Return current models plus an exact selected deprecated/custom model."""
    effective = _effective_catalog(catalog)
    selected = str(selected_model or "").lower()
    result = [
        {
            "id": item["id"],
            "display_name": item["display_name"],
            "status": item["status"],
            "lifecycle_note": item.get("lifecycle_note"),
        }
        for item in effective["models"]
        if item["status"] == "current"
    ]
    if selected and selected not in {item["id"] for item in result}:
        metadata = _model_metadata_from(effective, selected)
        result.append(
            {
                "id": selected,
                "display_name": metadata.get("display_name", selected),
                "status": metadata["status"],
                "lifecycle_note": metadata.get("lifecycle_note"),
            }
        )
    return result


def _sampling_rank(capability: dict[str, Any]) -> tuple[int, frozenset[str]]:
    support = capability["support"]
    if support == "always":
        return 3, frozenset()
    if support == "conditional":
        return 2, frozenset(capability["allowed_reasoning_efforts"] or [])
    if support == "undocumented":
        return 1, frozenset()
    return 0, frozenset()


def validate_catalog_transition(
    current: dict[str, Any] | None, candidate: dict[str, Any]
) -> None:
    """Prevent downloaded metadata from silently narrowing durable capabilities."""
    before = _effective_catalog(current)
    after = _effective_catalog(validate_catalog(candidate))
    ids = {item["id"] for item in before["models"]} | {
        item["id"] for item in after["models"]
    }
    for model_id in ids:
        old = _model_metadata_from(before, model_id)
        new = _model_metadata_from(after, model_id)
        if not set(old["reasoning"]["efforts"]).issubset(new["reasoning"]["efforts"]):
            raise ValueError("Catalogue update cannot remove reasoning effort choices")
        for api in _APIS:
            if old["api"][api] and not new["api"][api]:
                raise ValueError("Catalogue update cannot remove an API path")
            if old["function_calling"][api] and not new["function_calling"][api]:
                raise ValueError(
                    "Catalogue update cannot remove function-calling support"
                )
        if old["limits"]["max_output_tokens"] > new["limits"]["max_output_tokens"]:
            raise ValueError(
                "Catalogue update cannot lower max output without migration"
            )
        for name in ("temperature", "top_p"):
            old_rank, old_allowed = _sampling_rank(old[name])
            new_rank, new_allowed = _sampling_rank(new[name])
            if old_rank > new_rank or (
                old[name]["support"] == new[name]["support"] == "conditional"
                and not old_allowed.issubset(new_allowed)
            ):
                raise ValueError(
                    f"Catalogue update cannot narrow {name} without migration"
                )


def activate_catalog(catalog: dict[str, Any] | None) -> None:
    global _active
    _active = BUNDLED_CATALOG if catalog is None else _effective_catalog(catalog)


def all_reasoning_efforts() -> list[str]:
    return list(
        dict.fromkeys(
            e for item in _active["models"] for e in item["reasoning"]["efforts"]
        )
    )


def model_metadata(model: str) -> dict[str, Any]:
    """Return exact-ID metadata; unknown IDs receive conservative capabilities."""
    return _model_metadata_from(_active, model)


def compatibility_capabilities(
    model: str, *, effort: str | None = None
) -> dict[str, Any]:
    """Expose legacy booleans derived from v2 without making them authoritative."""
    del effort
    metadata = model_metadata(model)
    return {
        "supports_top_p": metadata["top_p"]["support"] in {"always", "conditional"},
        "supports_temperature": metadata["temperature"]["support"]
        in {"always", "conditional"},
        "supports_max_tokens": False,
        "supports_max_completion_tokens": True,
        "supports_reasoning_effort": metadata["reasoning"]["supported"],
        "supports_service_tier": metadata["service_tier"],
        "reasoning_effort_options": list(metadata["reasoning"]["efforts"]),
        "api": deepcopy(metadata["api"]),
        "auto_api": metadata.get("auto_api"),
        "function_calling": deepcopy(metadata["function_calling"]),
        "reasoning": deepcopy(metadata["reasoning"]),
        "temperature": deepcopy(metadata["temperature"]),
        "top_p": deepcopy(metadata["top_p"]),
        "limits": deepcopy(metadata["limits"]),
        "streaming": metadata["streaming"],
        "output_tokens": deepcopy(metadata["output_tokens"]),
        "recommended_profile": deepcopy(metadata["recommended_profile"]),
        "status": metadata["status"],
        "alias_of": metadata.get("alias_of"),
        "lifecycle_note": metadata.get("lifecycle_note"),
    }
