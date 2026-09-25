"""Portable, bounded Request Rule packs; separate from full agent backups."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
import json
from typing import Any
from uuid import uuid4

from .request_rules import (
    MAX_RULES,
    RequestRules,
    _basic_normalize,
    _validate_total_pattern_states,
    validate_rule,
    validate_rule_groups,
    validate_wording_groups,
)

PACK_FORMAT = "extended_openai_request_rule_pack"
PACK_VERSION = 1
MAX_PACK_BYTES = 2 * 1024 * 1024


def export_rule_pack(
    manager: RequestRules,
    selection: str | None,
    group_id: str | None = None,
    rule_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Export selected canonical rules in relative global priority order."""
    snapshot = manager.snapshot()
    rules = snapshot["rules"]
    if selection == "all":
        selected = rules
    elif selection == "group":
        if group_id is not None and group_id not in {
            g["id"] for g in snapshot["groups"]
        }:
            raise ValueError("Unknown group")
        selected = [rule for rule in rules if rule["group_id"] == group_id]
    elif selection == "selected":
        if (
            not isinstance(rule_ids, Sequence)
            or isinstance(rule_ids, str)
            or not rule_ids
        ):
            raise ValueError("Choose at least one rule")
        if len(set(rule_ids)) != len(rule_ids) or set(rule_ids) - {
            r["id"] for r in rules
        }:
            raise ValueError("Unknown or duplicate selected rule")
        selected = [rule for rule in rules if rule["id"] in rule_ids]
    else:
        raise ValueError("Choose All rules, A group, or Selected rules")
    selected_groups = {rule["group_id"] for rule in selected if rule["group_id"]}
    groups = [g for g in snapshot["groups"] if g["id"] in selected_groups]
    portable_rules = []
    wording_phrases: list[str] = []
    for order, rule in enumerate(selected):
        matching = (
            snapshot["defaults"]
            if rule["matching_behavior"] == "defaults"
            else rule["matching"]
        )
        if (
            matching["wording_alternatives"]
            and rule["match_type"] != "sentence_pattern"
        ):
            wording_phrases.extend(
                _basic_normalize(phrase) for phrase in rule["phrases"]
            )
        portable_rules.append(
            {
                key: deepcopy(value)
                for key, value in rule.items()
                if key not in {"slots", "matching_behavior", "matching", "order"}
            }
            | {
                "matching_behavior": "custom",
                "matching": deepcopy(matching),
                "order": order,
            }
        )
    required_wording = [
        group
        for group in snapshot["wording_groups"]
        if any(
            f" {_basic_normalize(term)} " in f" {phrase} "
            for phrase in wording_phrases
            for term in [group["canonical"], *group["alternatives"]]
        )
    ]
    pack = {
        "format": PACK_FORMAT,
        "version": PACK_VERSION,
        "groups": deepcopy(groups),
        "wording_groups": deepcopy(required_wording),
        "rules": portable_rules,
    }
    if not portable_rules:
        raise ValueError("Choose at least one rule to export")
    if len(json.dumps(pack, ensure_ascii=False).encode("utf-8")) > MAX_PACK_BYTES:
        raise ValueError("Rule pack exceeds the 2 MB safety limit")
    return pack


def _migrate_pack(value: Mapping[str, Any]) -> Mapping[str, Any]:
    """Version dispatch point for future portable formats."""
    if type(value.get("version")) is not int or value["version"] != PACK_VERSION:
        raise ValueError("Unsupported Request Rule pack version")
    return value


def validate_rule_pack(value: Any) -> dict[str, Any]:
    """Fail closed before review or import, using the persisted rule validator."""
    if isinstance(value, str):
        if len(value.encode("utf-8")) > MAX_PACK_BYTES:
            raise ValueError("Rule pack exceeds the 2 MB safety limit")
        try:
            value = json.loads(value)
        except (TypeError, ValueError, RecursionError) as err:
            raise ValueError("Rule pack is not valid JSON") from err
    if not isinstance(value, Mapping):
        raise ValueError("Rule pack must be an object")
    if set(value) != {"format", "version", "groups", "wording_groups", "rules"}:
        raise ValueError("Rule pack has missing or unknown fields")
    try:
        size = len(json.dumps(value, ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError, RecursionError) as err:
        raise ValueError("Rule pack contains invalid or overly nested data") from err
    if size > MAX_PACK_BYTES:
        raise ValueError("Rule pack exceeds the 2 MB safety limit")
    if value["format"] != PACK_FORMAT:
        raise ValueError("Unrecognized Request Rule pack format")
    migrated = _migrate_pack(value)
    groups = validate_rule_groups(migrated["groups"])
    wording_groups = validate_wording_groups(migrated["wording_groups"])
    raw_rules = migrated["rules"]
    if not isinstance(raw_rules, list) or not raw_rules or len(raw_rules) > MAX_RULES:
        raise ValueError("Rule pack must contain 1 to 500 rules")
    required_rule_fields = {
        "id",
        "name",
        "enabled",
        "phrases",
        "match_type",
        "action_type",
        "action",
        "matching_behavior",
        "matching",
        "order",
        "conditions",
        "group_id",
        "continue_matching",
        "ai_input_mode",
        "ai_input_capture",
    }
    if any(
        not isinstance(rule, Mapping) or not required_rule_fields <= set(rule)
        for rule in raw_rules
    ):
        raise ValueError("Rule pack rule is missing required fields")
    rules = [validate_rule(rule) for rule in raw_rules]
    if len({rule["id"] for rule in rules}) != len(rules):
        raise ValueError("Rule pack has duplicate rule IDs")
    if {rule["order"] for rule in rules} != set(range(len(rules))):
        raise ValueError("Rule pack priorities must be contiguous")
    known_groups = {group["id"] for group in groups}
    if any(rule["group_id"] and rule["group_id"] not in known_groups for rule in rules):
        raise ValueError("Rule pack references an unknown group")
    if any(rule["matching_behavior"] != "custom" for rule in rules):
        raise ValueError("Rule pack must include effective matching settings")
    _validate_total_pattern_states(rules)
    return {
        "groups": groups,
        "wording_groups": wording_groups,
        "rules": sorted(rules, key=lambda rule: rule["order"]),
    }


async def async_append_rule_pack(
    manager: RequestRules, prepared: Mapping[str, Any], *, expected_revision: str | None
) -> dict[str, Any]:
    """Append disabled rules under one lock and one rollback-capable save."""
    async with manager._lock:
        manager._require_revision_locked(expected_revision)
        incoming = prepared["rules"]
        if len(manager._rules) + len(incoming) > MAX_RULES:
            raise ValueError("Request Rule limit reached")
        current_names = {
            group["name"].casefold(): group["id"] for group in manager._groups
        }
        incoming_groups = []
        group_ids = {}
        for group in prepared["groups"]:
            existing = current_names.get(group["name"].casefold())
            assigned = existing or uuid4().hex
            group_ids[group["id"]] = assigned
            if not existing:
                incoming_groups.append({"id": assigned, "name": group["name"]})
                current_names[group["name"].casefold()] = assigned
        merged_groups = validate_rule_groups([*manager._groups, *incoming_groups])
        current_wording = deepcopy(manager._wording_groups)
        for group in prepared["wording_groups"]:
            if group not in current_wording:
                current_wording.append(group)
        merged_wording = validate_wording_groups(current_wording)
        new_rules = []
        for index, source in enumerate(incoming):
            candidate = {
                **source,
                "id": uuid4().hex,
                "enabled": False,
                "group_id": group_ids.get(source["group_id"]),
                "order": len(manager._rules) + index,
            }
            new_rules.append(validate_rule(candidate))
        _validate_total_pattern_states([*manager._rules, *new_rules])
        manager._groups = merged_groups
        manager._wording_groups = merged_wording
        manager._rules.extend(new_rules)
        manager._sort_and_compile()
        await manager._async_save_locked()
        return {
            "rules": deepcopy(new_rules),
            "groups": deepcopy(merged_groups),
            "wording_groups": deepcopy(merged_wording),
            "revision": manager.revision(),
        }
