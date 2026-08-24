"""Fast local Request Rules for conversation routing."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from difflib import SequenceMatcher
import logging
import re
from time import monotonic
from typing import Any, cast
import unicodedata
from uuid import uuid4

from hassil import SlotList, WildcardSlotList, is_match, parse_sentence
from hassil.expression import Group, RuleReference, Sentence

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store

from .const import (
    CONF_CHAT_MODEL,
    CONF_REASONING_EFFORT,
    DEFAULT_CHAT_MODEL,
    DEFAULT_CONVERSATION_TIMEOUT_MINUTES,
    REASONING_EFFORT_OPTIONS,
)
from .guest_mode import (
    GUEST_MODE_UNAVAILABLE,
    GuestCapabilityPolicy,
    guest_arguments_allowed_runtime,
)
from .ha_actions import async_execute_ha_actions
from .helpers import get_model_config
from .protected_actions import ProtectedActionRequired, async_require_protection

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 3
STORAGE_KEY_PREFIX = "extended_openai_conversation_responses.request_rules"
MAX_RULES = 500
MAX_PHRASES = 25
MAX_ACTIONS = 20
MATCH_TYPES = ("equals", "starts_with", "ends_with", "contains", "sentence_pattern")
ACTION_TYPES = ("local_action", "model_routing")
SLOT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
SLOT_REFERENCE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]{0,63})\}")
ROUTING_SCOPES = ("request", "conversation")
DEFAULT_MATCHING = {
    "word_forms": True,
    "wording_alternatives": True,
    "fuzzy": False,
    "fuzzy_threshold": 90,
}

# Phrase mappings are deliberately small and directional.  Both sides normalize to
# the same canonical wording, which keeps matching predictable and extensible.
DEFAULT_WORDING_GROUPS: tuple[dict[str, Any], ...] = (
    {"canonical": "turn on", "alternatives": ["switch on"]},
    {"canonical": "turn off", "alternatives": ["switch off"]},
    {"canonical": "close", "alternatives": ["shut"]},
    {"canonical": "tv", "alternatives": ["television"]},
    {"canonical": "increase", "alternatives": ["raise", "turn up"]},
    {"canonical": "decrease", "alternatives": ["lower", "turn down"]},
)
SENSITIVE_DOMAINS = {"lock", "alarm_control_panel"}


@dataclass(frozen=True, slots=True)
class RuleMatch:
    """The single winning rule and how it matched."""

    rule: dict[str, Any]
    phrase: str
    fuzzy: bool
    score: float
    slots: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CompiledPhrase:
    """One normalized phrase or parsed Hassil sentence pattern."""

    original: str
    normalized: str | None = None
    sentence: Sentence | None = None
    slot_lists: dict[str, SlotList] = field(default_factory=dict)
    slot_pattern: re.Pattern[str] | None = None


@dataclass(frozen=True, slots=True)
class RuleEvaluation:
    """Result of evaluating one utterance."""

    match: RuleMatch
    consume: bool
    response: str | None = None
    request_override: dict[str, str] | None = None


class RequestRuleStore(Store[dict[str, Any]]):
    """Versioned private Home Assistant storage."""

    async def _async_migrate_func(
        self, old_major_version: int, old_minor_version: int, old_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Migrate additive Request Rule storage changes."""
        if old_major_version == 1:
            return {
                **old_data,
                "wording_groups": _copy_wording_groups(DEFAULT_WORDING_GROUPS),
            }
        if old_major_version == 2:
            return old_data
        raise NotImplementedError


class RequestRules:
    """Concurrency-safe persisted rules with precomputed matcher state."""

    def __init__(self, store: RequestRuleStore) -> None:
        self._store = store
        self._rules: list[dict[str, Any]] = []
        self._defaults = dict(DEFAULT_MATCHING)
        self._wording_groups = _copy_wording_groups(DEFAULT_WORDING_GROUPS)
        self._compiled: list[
            tuple[dict[str, Any], dict[str, Any], list[CompiledPhrase]]
        ] = []
        self._lock = asyncio.Lock()
        self._initialized = False

    async def async_initialize(self) -> None:
        """Load and validate stored rules once."""
        async with self._lock:
            if self._initialized:
                return
            stored = await self._store.async_load()
            if isinstance(stored, Mapping):
                try:
                    self._defaults = validate_matching_settings(
                        stored.get("defaults", DEFAULT_MATCHING)
                    )
                except ValueError:
                    _LOGGER.warning("Ignoring invalid stored Request Rule defaults")
                try:
                    self._wording_groups = validate_wording_groups(
                        stored.get("wording_groups", DEFAULT_WORDING_GROUPS)
                    )
                except ValueError:
                    _LOGGER.warning(
                        "Ignoring invalid stored Request Rule wording groups"
                    )
                for raw in stored.get("rules", []):
                    try:
                        self._rules.append(validate_rule(raw))
                    except ValueError as err:
                        _LOGGER.warning("Ignoring invalid stored Request Rule: %s", err)
            self._sort_and_compile()
            self._initialized = True

    def snapshot(self) -> dict[str, Any]:
        """Return a copy suitable for the management API."""
        return {
            "storage_version": STORAGE_VERSION,
            "defaults": dict(self._defaults),
            "wording_groups": _copy_wording_groups(self._wording_groups),
            "rules": [dict(rule) for rule in self._rules],
        }

    async def async_backup_data(self) -> dict[str, Any]:
        """Return durable Request Rule state for the per-agent backup."""
        return self.snapshot()

    @staticmethod
    def validate_backup_data(value: Any) -> dict[str, Any]:
        """Validate backup state without mutating the live manager."""
        if not isinstance(value, Mapping):
            raise ValueError("request_rules must be an object")
        unknown = set(value) - {
            "storage_version",
            "defaults",
            "wording_groups",
            "rules",
        }
        if unknown:
            raise ValueError("unknown request_rules fields")
        defaults = validate_matching_settings(value.get("defaults", DEFAULT_MATCHING))
        wording_groups = validate_wording_groups(
            value.get("wording_groups", DEFAULT_WORDING_GROUPS)
        )
        raw_rules = value.get("rules", [])
        if not isinstance(raw_rules, Sequence) or isinstance(raw_rules, str):
            raise ValueError("request_rules.rules must be a list")
        if len(raw_rules) > MAX_RULES:
            raise ValueError("Request Rule limit reached")
        rules = [validate_rule(item) for item in raw_rules]
        if len({rule["id"] for rule in rules}) != len(rules):
            raise ValueError("duplicate Request Rule id")
        return {"defaults": defaults, "wording_groups": wording_groups, "rules": rules}

    async def async_replace_backup(self, value: Any) -> None:
        """Replace all durable state from a fully validated backup."""
        prepared = self.validate_backup_data(value)
        async with self._lock:
            self._defaults = prepared["defaults"]
            self._wording_groups = prepared["wording_groups"]
            self._rules = prepared["rules"]
            self._sort_and_compile()
            self._initialized = True
            await self._async_save_locked()

    async def async_set_defaults(self, value: Any) -> dict[str, Any]:
        """Replace global matching defaults."""
        defaults = validate_matching_settings(value)
        async with self._lock:
            self._defaults = defaults
            self._sort_and_compile()
            await self._async_save_locked()
        return dict(defaults)

    async def async_set_wording_groups(self, value: Any) -> list[dict[str, Any]]:
        """Replace the persisted wording synonym groups."""
        groups = validate_wording_groups(value)
        async with self._lock:
            self._wording_groups = groups
            self._sort_and_compile()
            await self._async_save_locked()
        return _copy_wording_groups(groups)

    async def async_create(self, value: Any) -> dict[str, Any]:
        """Create one rule."""
        if not isinstance(value, Mapping):
            raise ValueError("rule must be an object")
        raw = dict(value)
        raw.setdefault("id", uuid4().hex)
        raw.setdefault("order", len(self._rules))
        rule = validate_rule(raw)
        async with self._lock:
            if len(self._rules) >= MAX_RULES:
                raise ValueError("Request Rule limit reached")
            if any(item["id"] == rule["id"] for item in self._rules):
                raise ValueError("rule id already exists")
            self._rules.append(rule)
            self._sort_and_compile()
            await self._async_save_locked()
        return dict(rule)

    async def async_update(self, rule_id: str, value: Any) -> dict[str, Any]:
        """Replace one rule while preserving its id."""
        if not isinstance(value, Mapping):
            raise ValueError("rule must be an object")
        async with self._lock:
            index = self._index(rule_id)
            raw = dict(value)
            raw["id"] = rule_id
            raw.setdefault("order", self._rules[index]["order"])
            rule = validate_rule(raw)
            self._rules[index] = rule
            self._sort_and_compile()
            await self._async_save_locked()
        return dict(rule)

    async def async_delete(self, rule_id: str) -> bool:
        """Delete one rule."""
        async with self._lock:
            index = self._index(rule_id)
            del self._rules[index]
            self._sort_and_compile()
            await self._async_save_locked()
        return True

    async def async_duplicate(self, rule_id: str) -> dict[str, Any]:
        """Duplicate one rule immediately after its source."""
        async with self._lock:
            source = dict(self._rules[self._index(rule_id)])
        source.update(
            id=uuid4().hex,
            name=f"{source['name']} copy",
            order=int(source["order"]) + 1,
        )
        return await self.async_create(source)

    def match(self, text: str) -> RuleMatch | None:
        """Select one deterministic winner, using fuzzy only as a fallback."""
        deterministic: list[tuple[tuple[int, int, int], RuleMatch]] = []
        fuzzy: list[tuple[tuple[float, int, int], RuleMatch]] = []
        rank = {
            "equals": 5,
            "sentence_pattern": 4,
            "starts_with": 3,
            "ends_with": 2,
            "contains": 1,
        }
        for rule, settings, phrases in self._compiled:
            candidate = (
                ""
                if rule["match_type"] == "sentence_pattern"
                else normalize_text(text, settings, self._wording_groups)
            )
            for compiled in phrases:
                if compiled.sentence is not None:
                    if compiled.slot_pattern is not None:
                        slot_match = compiled.slot_pattern.fullmatch(text)
                        if slot_match is None:
                            continue
                        slots = {
                            name: value.strip()
                            for name, value in slot_match.groupdict().items()
                        }
                    else:
                        context = is_match(
                            text,
                            compiled.sentence,
                            slot_lists=compiled.slot_lists,
                            expansion_rules={},
                        )
                        if context is None:
                            continue
                        slots = {
                            entity.name: str(entity.value).strip()
                            for entity in context.entities
                        }
                    result = RuleMatch(rule, compiled.original, False, 100.0, slots)
                    deterministic.append(
                        (
                            (
                                rank[rule["match_type"]],
                                len(compiled.original),
                                -rule["order"],
                            ),
                            result,
                        )
                    )
                    continue
                phrase = cast(str, compiled.normalized)
                if _deterministic_match(candidate, phrase, rule["match_type"]):
                    result = RuleMatch(rule, compiled.original, False, 100.0)
                    deterministic.append(
                        (
                            (rank[rule["match_type"]], len(phrase), -rule["order"]),
                            result,
                        )
                    )
                    continue
                if settings["fuzzy"]:
                    score = _fuzzy_score(candidate, phrase, rule["match_type"])
                    if score >= settings["fuzzy_threshold"]:
                        result = RuleMatch(rule, compiled.original, True, score)
                        fuzzy.append(
                            ((score, rank[rule["match_type"]], -rule["order"]), result)
                        )
        if deterministic:
            return max(deterministic, key=lambda item: item[0])[1]
        if fuzzy:
            return max(fuzzy, key=lambda item: item[0])[1]
        return None

    def _index(self, rule_id: str) -> int:
        for index, rule in enumerate(self._rules):
            if rule["id"] == rule_id:
                return index
        raise ValueError("Request Rule not found")

    def _sort_and_compile(self) -> None:
        self._rules.sort(key=lambda item: (item["order"], item["name"].casefold()))
        self._compiled = []
        for rule in self._rules:
            if not rule["enabled"]:
                continue
            settings = (
                self._defaults
                if rule["matching_behavior"] == "defaults"
                else rule["matching"]
            )
            phrases = (
                [_compile_sentence_pattern(phrase) for phrase in rule["phrases"]]
                if rule["match_type"] == "sentence_pattern"
                else [
                    CompiledPhrase(
                        phrase, normalize_text(phrase, settings, self._wording_groups)
                    )
                    for phrase in rule["phrases"]
                ]
            )
            self._compiled.append((rule, settings, phrases))

    async def _async_save_locked(self) -> None:
        await self._store.async_save(
            {
                "defaults": self._defaults,
                "wording_groups": self._wording_groups,
                "rules": self._rules,
            }
        )


class RequestRuleRuntime:
    """Per-agent, in-memory conversation routing overrides."""

    def __init__(self) -> None:
        self._conversation_overrides: dict[str, tuple[dict[str, str], float, int]] = {}

    def get(
        self,
        session_id: str,
        timeout_minutes: int = DEFAULT_CONVERSATION_TIMEOUT_MINUTES,
    ) -> dict[str, str]:
        now = monotonic()
        for key, (_, last_used, stored_timeout) in list(
            self._conversation_overrides.items()
        ):
            if now - last_used >= max(1, stored_timeout) * 60:
                self._conversation_overrides.pop(key, None)
        entry = self._conversation_overrides.get(session_id)
        if entry is None:
            return {}
        values, _, _ = entry
        self._conversation_overrides[session_id] = (
            values,
            now,
            max(1, timeout_minutes),
        )
        return dict(values)

    def set(
        self,
        session_id: str,
        override: Mapping[str, str],
        timeout_minutes: int = DEFAULT_CONVERSATION_TIMEOUT_MINUTES,
    ) -> None:
        values = {**self.get(session_id, timeout_minutes), **dict(override)}
        self._conversation_overrides[session_id] = (
            values,
            monotonic(),
            max(1, timeout_minutes),
        )

    def reset(self, session_id: str) -> None:
        self._conversation_overrides.pop(session_id, None)

    def effective_options(
        self,
        defaults: Mapping[str, Any],
        session_id: str,
        request_override: Mapping[str, str] | None = None,
        timeout_minutes: int = DEFAULT_CONVERSATION_TIMEOUT_MINUTES,
    ) -> dict[str, Any]:
        """Apply documented request > conversation > configured precedence."""
        return {
            **defaults,
            **self.get(session_id, timeout_minutes),
            **dict(request_override or {}),
        }


def request_rule_session_id(continuity_key: str | None, conversation_id: str) -> str:
    """Use the resolved continuity identity, or Core's actual ChatLog identity."""
    return (
        f"continuity:{continuity_key}"
        if continuity_key
        else f"conversation:{conversation_id}"
    )


def validate_matching_settings(value: Any) -> dict[str, Any]:
    """Validate global or custom lightweight matching settings."""
    if not isinstance(value, Mapping):
        raise ValueError("matching settings must be an object")
    unknown = set(value) - set(DEFAULT_MATCHING)
    if unknown:
        raise ValueError("unknown matching settings: " + ", ".join(sorted(unknown)))
    result = {**DEFAULT_MATCHING, **value}
    for key in ("word_forms", "wording_alternatives", "fuzzy"):
        if not isinstance(result[key], bool):
            raise ValueError(f"{key} must be true or false")
    threshold = result["fuzzy_threshold"]
    if (
        isinstance(threshold, bool)
        or not isinstance(threshold, int)
        or not 70 <= threshold <= 100
    ):
        raise ValueError("fuzzy_threshold must be an integer from 70 to 100")
    return result


def validate_wording_groups(value: Any) -> list[dict[str, Any]]:
    """Validate an unambiguous, bounded synonym-group catalog."""
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError("wording_groups must be a list")
    if len(value) > 100:
        raise ValueError("wording_groups may contain at most 100 groups")
    result: list[dict[str, Any]] = []
    claimed: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping) or set(item) != {"canonical", "alternatives"}:
            raise ValueError("each wording group needs canonical and alternatives")
        canonical = _clean(item["canonical"], 100, "canonical wording")
        raw_alternatives = item["alternatives"]
        if not isinstance(raw_alternatives, Sequence) or isinstance(
            raw_alternatives, (str, bytes)
        ):
            raise ValueError("wording alternatives must be a list")
        if not raw_alternatives or len(raw_alternatives) > 25:
            raise ValueError("wording alternatives must contain 1 to 25 items")
        alternatives = list(
            dict.fromkeys(
                _clean(item, 100, "alternative wording") for item in raw_alternatives
            )
        )
        terms = [canonical, *alternatives]
        normalized = [_basic_normalize(term) for term in terms]
        if len(set(normalized)) != len(normalized) or claimed.intersection(normalized):
            raise ValueError("wording groups contain an ambiguous duplicate phrase")
        claimed.update(normalized)
        result.append({"canonical": canonical, "alternatives": alternatives})
    return result


def _copy_wording_groups(value: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "canonical": str(group["canonical"]),
            "alternatives": list(group["alternatives"]),
        }
        for group in value
    ]


def validate_rule(value: Any) -> dict[str, Any]:
    """Validate and normalize the persisted rule contract."""
    if not isinstance(value, Mapping):
        raise ValueError("rule must be an object")
    allowed = {
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
        "slots",
    }
    unknown = set(value) - allowed
    if unknown:
        raise ValueError("unknown rule fields: " + ", ".join(sorted(unknown)))
    rule_id = _clean(value.get("id"), 64, "id")
    name = _clean(value.get("name"), 120, "name")
    phrases_value = value.get("phrases")
    if not isinstance(phrases_value, Sequence) or isinstance(phrases_value, str):
        raise ValueError("phrases must be a list")
    phrases = list(dict.fromkeys(_clean(item, 200, "phrase") for item in phrases_value))
    if not phrases or len(phrases) > MAX_PHRASES:
        raise ValueError(f"phrases must contain 1 to {MAX_PHRASES} items")
    match_type = value.get("match_type", "equals")
    if match_type not in MATCH_TYPES:
        raise ValueError("unsupported match type")
    if match_type == "sentence_pattern":
        compiled_phrases = [_compile_sentence_pattern(phrase) for phrase in phrases]
        phrase_slots = [set(item.slot_lists) for item in compiled_phrases]
        if any(names != phrase_slots[0] for names in phrase_slots[1:]):
            raise ValueError("all sentence variants must capture the same slots")
        slot_names = sorted(phrase_slots[0])
    else:
        slot_names = []
        if any(SLOT_REFERENCE.search(phrase) for phrase in phrases):
            raise ValueError("variable values require Home Assistant sentence matching")
    action_type = value.get("action_type", "local_action")
    if action_type not in ACTION_TYPES:
        raise ValueError("unsupported action type")
    action = _validate_action(action_type, value.get("action", {}))
    referenced_slots = _referenced_slots(action)
    unknown_slots = referenced_slots - set(slot_names)
    if unknown_slots:
        raise ValueError("unknown captured value: " + ", ".join(sorted(unknown_slots)))
    if (
        action_type == "model_routing"
        and match_type in {"equals", "sentence_pattern"}
        and action["scope"] == "request"
        and not action["reset"]
    ):
        raise ValueError(
            "Exact AI routing commands must apply to the rest of the conversation"
        )
    behavior = value.get("matching_behavior", "defaults")
    if behavior not in {"defaults", "custom"}:
        raise ValueError("matching_behavior must be defaults or custom")
    matching = validate_matching_settings(value.get("matching", DEFAULT_MATCHING))
    order = value.get("order", 0)
    if isinstance(order, bool) or not isinstance(order, int) or order < 0:
        raise ValueError("order must be a non-negative integer")
    enabled = value.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ValueError("enabled must be true or false")
    return {
        "id": rule_id,
        "name": name,
        "enabled": enabled,
        "phrases": phrases,
        "match_type": match_type,
        "action_type": action_type,
        "action": action,
        "matching_behavior": behavior,
        "matching": matching,
        "order": order,
        "slots": [{"name": name} for name in slot_names],
    }


def _validate_action(action_type: str, value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("action must be an object")
    if action_type == "local_action":
        allowed = {
            "actions",
            "success_response",
            "failure_response",
            "canonical_signature",
        }
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(
                "unknown local action fields: " + ", ".join(sorted(unknown))
            )
        actions_value = value.get("actions")
        if not isinstance(actions_value, Sequence) or isinstance(actions_value, str):
            raise ValueError("actions must be a list")
        if not actions_value or len(actions_value) > MAX_ACTIONS:
            raise ValueError(f"actions must contain 1 to {MAX_ACTIONS} items")
        actions = [_validate_local_action(item) for item in actions_value]
        return {
            "actions": actions,
            "success_response": _clean(
                value.get("success_response", "Done"), 500, "success_response"
            ),
            "failure_response": _clean(
                value.get("failure_response", "Sorry, that did not work"),
                500,
                "failure_response",
            ),
            "canonical_signature": canonical_action_signature(actions),
        }
    allowed = {"model", "reasoning_effort", "scope", "reset", "success_response"}
    unknown = set(value) - allowed
    if unknown:
        raise ValueError("unknown model routing fields: " + ", ".join(sorted(unknown)))
    reset = value.get("reset", False)
    if not isinstance(reset, bool):
        raise ValueError("reset must be true or false")
    scope = value.get("scope", "request")
    if scope not in ROUTING_SCOPES:
        raise ValueError("unsupported routing scope")
    model = str(value.get("model") or "").strip()
    effort = str(value.get("reasoning_effort") or "").strip()
    if reset:
        model = ""
        effort = ""
    if not reset and not model and not effort:
        raise ValueError("model routing must set a model or reasoning effort")
    if effort and effort not in REASONING_EFFORT_OPTIONS:
        raise ValueError("unsupported reasoning effort")
    if (
        effort
        and model
        and not get_model_config(model).get("supports_reasoning_effort")
    ):
        raise ValueError(f"model {model} does not support reasoning effort")
    return {
        "model": model or None,
        "reasoning_effort": effort or None,
        "scope": scope,
        "reset": reset,
        "success_response": _clean(
            value.get(
                "success_response",
                "Using the configured defaults" if reset else "Updated",
            ),
            500,
            "success_response",
        ),
    }


def _validate_ha_action(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("each Home Assistant action must be an object")
    unknown = set(value) - {"domain", "service", "target", "data"}
    if unknown:
        raise ValueError(
            "unknown Home Assistant action fields: " + ", ".join(sorted(unknown))
        )
    domain = _clean(value.get("domain"), 64, "domain")
    service = _clean(value.get("service"), 64, "service")
    result = {
        "domain": domain,
        "service": service,
        "target": dict(value.get("target") or {}),
        "data": dict(value.get("data") or {}),
    }
    if not re.fullmatch(r"[a-z0-9_]+", domain) or not re.fullmatch(
        r"[a-z0-9_]+", service
    ):
        raise ValueError("action domain and service must use lowercase slugs")
    if not isinstance(value.get("target", {}), Mapping) or not isinstance(
        value.get("data", {}), Mapping
    ):
        raise ValueError("action target and data must be objects")
    return result


def _validate_local_action(value: Any) -> dict[str, Any]:
    """Validate one HA or configured-function action in an ordered rule."""
    if isinstance(value, Mapping) and value.get("type") == "function":
        unknown = set(value) - {"type", "function", "arguments"}
        if unknown:
            raise ValueError(
                "unknown function action fields: " + ", ".join(sorted(unknown))
            )
        function_name = _clean(value.get("function"), 120, "function")
        arguments = value.get("arguments", {})
        if not isinstance(arguments, Mapping):
            raise ValueError("function arguments must be an object")
        normalized_arguments: dict[str, Any] = {}
        for name, binding in arguments.items():
            if not isinstance(name, str) or not SLOT_NAME.fullmatch(name):
                raise ValueError("function argument names must be simple identifiers")
            if not isinstance(binding, Mapping):
                normalized_arguments[name] = {"source": "fixed", "value": binding}
                continue
            source = binding.get("source", "fixed")
            if source == "slot":
                if set(binding) != {"source", "slot"}:
                    raise ValueError("slot arguments need only source and slot")
                slot = binding.get("slot")
                if not isinstance(slot, str) or not SLOT_NAME.fullmatch(slot):
                    raise ValueError("slot argument must name a captured value")
                normalized_arguments[name] = {"source": "slot", "slot": slot}
            elif source == "fixed":
                if set(binding) - {"source", "value"}:
                    raise ValueError("fixed arguments need only source and value")
                normalized_arguments[name] = {
                    "source": "fixed",
                    "value": binding.get("value"),
                }
            else:
                raise ValueError("function argument source must be fixed or slot")
        return {
            "type": "function",
            "function": function_name,
            "arguments": normalized_arguments,
        }
    if isinstance(value, Mapping) and value.get("type") == "home_assistant":
        value = {key: item for key, item in value.items() if key != "type"}
    return _validate_ha_action(value)


def _referenced_slots(value: Any) -> set[str]:
    """Collect deterministic slot references from a persisted rule value."""
    if isinstance(value, str):
        return set(SLOT_REFERENCE.findall(value))
    if isinstance(value, Mapping):
        if value.get("source") == "slot" and isinstance(value.get("slot"), str):
            return {str(value["slot"])}
        if value.get("value_from") == "slot" and isinstance(value.get("slot"), str):
            return {str(value["slot"])}
        result: set[str] = set()
        for item in value.values():
            result.update(_referenced_slots(item))
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        result = set()
        for item in value:
            result.update(_referenced_slots(item))
        return result
    return set()


def resolve_slot_values(value: Any, slots: Mapping[str, str]) -> Any:
    """Resolve safe slot references recursively without evaluating templates."""
    if isinstance(value, str):
        return SLOT_REFERENCE.sub(lambda match: slots[match.group(1)], value)
    if isinstance(value, Mapping):
        if set(value) == {"value_from", "slot"} and value.get("value_from") == "slot":
            return slots[str(value["slot"])]
        return {key: resolve_slot_values(item, slots) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve_slot_values(item, slots) for item in value]
    return value


def resolve_function_arguments(
    arguments: Mapping[str, Mapping[str, Any]], slots: Mapping[str, str]
) -> dict[str, Any]:
    """Resolve fixed and request-backed function arguments."""
    return {
        name: (
            slots[str(binding["slot"])]
            if binding.get("source") == "slot"
            else binding.get("value")
        )
        for name, binding in arguments.items()
    }


def canonical_action_signature(actions: Sequence[Mapping[str, Any]]) -> str:
    """Stable action identity for future Suggested Local Commands comparisons."""
    import json

    normalized = []
    for action in actions:
        if action.get("type") == "function":
            normalized.append(
                {
                    "type": "function",
                    "function": action["function"],
                    "arguments": action.get("arguments", {}),
                }
            )
        else:
            normalized.append(
                {
                    "domain": action["domain"],
                    "service": action["service"],
                    "target": action.get("target", {}),
                    "data": action.get("data", {}),
                }
            )
    return json.dumps(normalized, sort_keys=True, separators=(",", ":"))


def rule_has_sensitive_actions(rule: Mapping[str, Any]) -> bool:
    """Flag tolerant matching for obvious security-sensitive local domains."""
    if rule.get("action_type") != "local_action":
        return False
    actions = cast(Mapping[str, Any], rule.get("action", {})).get("actions", [])
    for action in actions:
        domain = action.get("domain")
        service = str(action.get("service", "")).casefold()
        if domain in SENSITIVE_DOMAINS or (
            domain == "cover" and any(term in service for term in ("open", "close"))
        ):
            return True
    return False


async def async_evaluate_rule(
    hass: HomeAssistant,
    rules: RequestRules,
    runtime: RequestRuleRuntime,
    text: str,
    session_id: str,
    configured_model: str = DEFAULT_CHAT_MODEL,
    guest_policy: GuestCapabilityPolicy | None = None,
    timeout_minutes: int = DEFAULT_CONVERSATION_TIMEOUT_MINUTES,
    function_executor: Callable[[str, dict[str, Any]], Awaitable[Any]] | None = None,
) -> RuleEvaluation | None:
    """Match and apply local side effects or model-routing state."""
    match = rules.match(text)
    if match is None:
        return None
    rule = match.rule
    action = rule["action"]
    if rule["action_type"] == "local_action":
        policy = guest_policy or GuestCapabilityPolicy.unrestricted()
        resolved_actions = [
            resolve_slot_values(configured_action, match.slots)
            for configured_action in action["actions"]
            if configured_action.get("type") != "function"
        ]
        if policy.guest_active:
            try:
                allowed = all(
                    guest_arguments_allowed_runtime(
                        hass,
                        configured_action,
                        policy,
                        control=True,
                        require_entity_selector=True,
                    )
                    for configured_action in resolved_actions
                )
            except Exception:
                _LOGGER.exception(
                    "Guest authorization failed for Request Rule %s", rule["id"]
                )
                allowed = False
            if not allowed:
                return RuleEvaluation(match, True, GUEST_MODE_UNAVAILABLE)
        try:
            await async_require_protection(resolved_actions)
            for configured_action in action["actions"]:
                if configured_action.get("type") == "function":
                    if function_executor is None:
                        raise HomeAssistantError("Function actions are unavailable")
                    await function_executor(
                        configured_action["function"],
                        resolve_function_arguments(
                            configured_action["arguments"], match.slots
                        ),
                    )
                else:
                    await async_execute_ha_actions(
                        hass, [resolve_slot_values(configured_action, match.slots)]
                    )
        except ProtectedActionRequired:
            raise
        except Exception:
            _LOGGER.exception("Request Rule local action failed for %s", rule["id"])
            return RuleEvaluation(
                match,
                True,
                resolve_slot_values(action["failure_response"], match.slots),
            )
        return RuleEvaluation(
            match, True, resolve_slot_values(action["success_response"], match.slots)
        )

    if action["reset"]:
        runtime.reset(session_id)
        return RuleEvaluation(
            match,
            rule["match_type"] in {"equals", "sentence_pattern"},
            action["success_response"],
        )
    override = {}
    if action["model"]:
        override[CONF_CHAT_MODEL] = action["model"]
    if action["reasoning_effort"]:
        selected_model = (
            action["model"]
            or runtime.get(session_id, timeout_minutes).get(CONF_CHAT_MODEL)
            or configured_model
        )
        if not get_model_config(selected_model).get("supports_reasoning_effort"):
            raise HomeAssistantError(
                f"Model {selected_model} does not support reasoning effort"
            )
        override[CONF_REASONING_EFFORT] = action["reasoning_effort"]
    combined_override = {
        **runtime.get(session_id, timeout_minutes),
        **override,
    }
    combined_model = combined_override.get(CONF_CHAT_MODEL, configured_model)
    if (
        combined_model
        and combined_override.get(CONF_REASONING_EFFORT)
        and not get_model_config(combined_model).get("supports_reasoning_effort")
    ):
        raise HomeAssistantError(
            f"Model {combined_model} does not support reasoning effort"
        )
    if action["scope"] == "conversation":
        runtime.set(session_id, override, timeout_minutes)
        request_override = None
    else:
        request_override = override
    consume = rule["match_type"] in {"equals", "sentence_pattern"}
    return RuleEvaluation(
        match,
        consume,
        action["success_response"] if consume else None,
        request_override,
    )


def _basic_normalize(text: str) -> str:
    """Normalize punctuation and spacing without semantic transformations."""
    value = unicodedata.normalize("NFKC", str(text)).casefold()
    value = value.replace("\u2019", "'").replace("-", " ")
    value = re.sub(r"[^\w\s']+", " ", value, flags=re.UNICODE)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def normalize_text(
    text: str,
    settings: Mapping[str, Any],
    wording_groups: Sequence[Mapping[str, Any]] = DEFAULT_WORDING_GROUPS,
) -> str:
    """Apply deterministic, conservative speech-text normalization."""
    value = _basic_normalize(text)
    if settings.get("wording_alternatives"):
        padded = f" {value} "
        replacements = sorted(
            (
                (_basic_normalize(alternative), _basic_normalize(group["canonical"]))
                for group in wording_groups
                for alternative in group["alternatives"]
            ),
            key=lambda item: len(item[0]),
            reverse=True,
        )
        for alternative, canonical in replacements:
            padded = padded.replace(f" {alternative} ", f" {canonical} ")
        value = padded.strip()
    if settings.get("word_forms"):
        value = " ".join(_singularize(token) for token in value.split())
    return value


def _compile_sentence_pattern(pattern: str) -> CompiledPhrase:
    """Parse supported Hassil syntax and configure wildcard slot capture."""
    try:
        sentence = parse_sentence(pattern)
    except Exception as err:
        raise ValueError(f"invalid sentence pattern: {err}") from err

    def has_rule_reference(expression: Any) -> bool:
        if isinstance(expression, RuleReference):
            return True
        if isinstance(expression, Group):
            return any(has_rule_reference(item) for item in expression.items)
        return False

    if has_rule_reference(sentence.expression):
        raise ValueError(
            "named expansion rules (<name>) are not supported in Request Rules"
        )
    slot_lists: dict[str, SlotList] = {
        name: WildcardSlotList(name=name)
        for name in dict.fromkeys(sentence.list_names())
    }
    return CompiledPhrase(
        pattern,
        sentence=sentence,
        slot_lists=slot_lists,
        slot_pattern=_compile_basic_slot_pattern(pattern) if slot_lists else None,
    )


def _compile_basic_slot_pattern(pattern: str) -> re.Pattern[str]:
    """Compile the supported Hassil subset with non-greedy named captures."""

    def compile_part(value: str) -> str:
        result = ""
        index = 0
        while index < len(value):
            char = value[index]
            if char in "[{(":
                closing = {"[": "]", "{": "}", "(": ")"}[char]
                end = value.find(closing, index + 1)
                if end < 0:
                    raise ValueError("invalid sentence pattern")
                inner = value[index + 1 : end]
                if char == "{":
                    if not SLOT_NAME.fullmatch(inner):
                        raise ValueError("invalid captured value name")
                    result += f"(?P<{inner}>.+?)"
                elif char == "[":
                    result += f"(?:{compile_part(inner)})?"
                else:
                    alternatives = inner.split("|")
                    result += (
                        "(?:"
                        + "|".join(compile_part(item) for item in alternatives)
                        + ")"
                    )
                index = end + 1
                continue
            if char.isspace():
                while index + 1 < len(value) and value[index + 1].isspace():
                    index += 1
                result += r"\s+"
            else:
                result += re.escape(char)
            index += 1
        return result

    return re.compile(r"\s*" + compile_part(pattern) + r"\s*", re.IGNORECASE)


def _singularize(token: str) -> str:
    if (
        token in {"news", "series", "species"}
        or len(token) <= 3
        or token.endswith(("ss", "us", "is"))
    ):
        return token
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if token.endswith(("ches", "shes", "xes", "zes", "ses")):
        return token[:-2]
    if token.endswith("s"):
        return token[:-1]
    return token


def _deterministic_match(text: str, phrase: str, match_type: str) -> bool:
    if match_type == "equals":
        return text == phrase
    if match_type == "starts_with":
        return text == phrase or text.startswith(phrase + " ")
    if match_type == "ends_with":
        return text == phrase or text.endswith(" " + phrase)
    return text == phrase or f" {phrase} " in f" {text} "


def _fuzzy_score(text: str, phrase: str, match_type: str) -> float:
    if match_type == "equals":
        candidates = [text]
    else:
        words = text.split()
        size = max(1, len(phrase.split()))
        if match_type == "starts_with":
            candidates = [" ".join(words[:size])]
        elif match_type == "ends_with":
            candidates = [" ".join(words[-size:])]
        else:
            candidates = [
                " ".join(words[index : index + size])
                for index in range(max(1, len(words) - size + 1))
            ]
    return max(
        (
            SequenceMatcher(None, phrase, candidate).ratio() * 100
            for candidate in candidates
        ),
        default=0.0,
    )


def _clean(value: Any, limit: int, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required")
    cleaned = value.strip()
    if len(cleaned) > limit:
        raise ValueError(f"{field} is too long")
    return cleaned


_MANAGERS = "extended_openai_conversation_responses.request_rule_managers"
_RUNTIMES = "extended_openai_conversation_responses.request_rule_runtimes"


async def async_get_request_rules(
    hass: HomeAssistant, entry_id: str, subentry_id: str
) -> RequestRules:
    """Return the shared initialized per-agent rule store."""
    managers = hass.data.setdefault(_MANAGERS, {})
    key = (entry_id, subentry_id)
    if key not in managers:
        managers[key] = RequestRules(
            RequestRuleStore(
                hass,
                STORAGE_VERSION,
                f"{STORAGE_KEY_PREFIX}.{entry_id}.{subentry_id}",
            )
        )
    manager = cast(RequestRules, managers[key])
    await manager.async_initialize()
    return manager


def get_request_rule_runtime(
    hass: HomeAssistant, entry_id: str, subentry_id: str
) -> RequestRuleRuntime:
    """Return per-agent transient conversation overrides."""
    runtimes = hass.data.setdefault(_RUNTIMES, {})
    return cast(
        RequestRuleRuntime,
        runtimes.setdefault((entry_id, subentry_id), RequestRuleRuntime()),
    )
