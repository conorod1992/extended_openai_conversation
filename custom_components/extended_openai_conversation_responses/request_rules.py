"""Fast local Request Rules for conversation routing."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
import logging
import re
from typing import Any, cast
import unicodedata
from uuid import uuid4

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store

from .const import (
    CONF_CHAT_MODEL,
    CONF_REASONING_EFFORT,
    DEFAULT_CHAT_MODEL,
    REASONING_EFFORT_OPTIONS,
)
from .ha_actions import async_execute_ha_actions
from .helpers import get_model_config

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
STORAGE_KEY_PREFIX = "extended_openai_conversation_responses.request_rules"
MAX_RULES = 500
MAX_PHRASES = 25
MAX_ACTIONS = 20
MATCH_TYPES = ("equals", "starts_with", "ends_with", "contains")
ACTION_TYPES = ("local_action", "model_routing")
ROUTING_SCOPES = ("request", "conversation")
DEFAULT_MATCHING = {
    "word_forms": True,
    "wording_alternatives": True,
    "fuzzy": False,
    "fuzzy_threshold": 90,
}

# Phrase mappings are deliberately small and directional.  Both sides normalize to
# the same canonical wording, which keeps matching predictable and extensible.
WORDING_ALTERNATIVES: tuple[tuple[str, str], ...] = (
    ("switch on", "turn on"),
    ("switch off", "turn off"),
    ("shut", "close"),
    ("television", "tv"),
    ("raise", "increase"),
    ("turn up", "increase"),
    ("lower", "decrease"),
    ("turn down", "decrease"),
)
SENSITIVE_DOMAINS = {"lock", "alarm_control_panel"}


@dataclass(frozen=True, slots=True)
class RuleMatch:
    """The single winning rule and how it matched."""

    rule: dict[str, Any]
    phrase: str
    fuzzy: bool
    score: float


@dataclass(frozen=True, slots=True)
class RuleEvaluation:
    """Result of evaluating one utterance."""

    match: RuleMatch
    consume: bool
    response: str | None = None
    request_override: dict[str, str] | None = None


class RequestRuleStore(Store[dict[str, Any]]):
    """Versioned private Home Assistant storage."""


class RequestRules:
    """Concurrency-safe persisted rules with precomputed matcher state."""

    def __init__(self, store: RequestRuleStore) -> None:
        self._store = store
        self._rules: list[dict[str, Any]] = []
        self._defaults = dict(DEFAULT_MATCHING)
        self._compiled: list[
            tuple[dict[str, Any], dict[str, Any], list[tuple[str, str]]]
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
        unknown = set(value) - {"storage_version", "defaults", "rules"}
        if unknown:
            raise ValueError("unknown request_rules fields")
        defaults = validate_matching_settings(value.get("defaults", DEFAULT_MATCHING))
        raw_rules = value.get("rules", [])
        if not isinstance(raw_rules, Sequence) or isinstance(raw_rules, str):
            raise ValueError("request_rules.rules must be a list")
        if len(raw_rules) > MAX_RULES:
            raise ValueError("Request Rule limit reached")
        rules = [validate_rule(item) for item in raw_rules]
        if len({rule["id"] for rule in rules}) != len(rules):
            raise ValueError("duplicate Request Rule id")
        return {"defaults": defaults, "rules": rules}

    async def async_replace_backup(self, value: Any) -> None:
        """Replace all durable state from a fully validated backup."""
        prepared = self.validate_backup_data(value)
        async with self._lock:
            self._defaults = prepared["defaults"]
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
        rank = {"equals": 4, "starts_with": 3, "ends_with": 2, "contains": 1}
        for rule, settings, phrases in self._compiled:
            candidate = normalize_text(text, settings)
            for original, phrase in phrases:
                if _deterministic_match(candidate, phrase, rule["match_type"]):
                    result = RuleMatch(rule, original, False, 100.0)
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
                        result = RuleMatch(rule, original, True, score)
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
            self._compiled.append(
                (
                    rule,
                    settings,
                    [
                        (phrase, normalize_text(phrase, settings))
                        for phrase in rule["phrases"]
                    ],
                )
            )

    async def _async_save_locked(self) -> None:
        await self._store.async_save({"defaults": self._defaults, "rules": self._rules})


class RequestRuleRuntime:
    """Per-agent, in-memory conversation routing overrides."""

    def __init__(self) -> None:
        self._conversation_overrides: dict[str, dict[str, str]] = {}

    def get(self, session_id: str) -> dict[str, str]:
        return dict(self._conversation_overrides.get(session_id, {}))

    def set(self, session_id: str, override: Mapping[str, str]) -> None:
        self._conversation_overrides[session_id] = dict(override)

    def reset(self, session_id: str) -> None:
        self._conversation_overrides.pop(session_id, None)

    def effective_options(
        self,
        defaults: Mapping[str, Any],
        session_id: str,
        request_override: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        """Apply documented request > conversation > configured precedence."""
        return {
            **defaults,
            **self.get(session_id),
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
    action_type = value.get("action_type", "local_action")
    if action_type not in ACTION_TYPES:
        raise ValueError("unsupported action type")
    action = _validate_action(action_type, value.get("action", {}))
    if (
        action_type == "model_routing"
        and match_type == "equals"
        and action["scope"] == "request"
        and not action["reset"]
    ):
        raise ValueError(
            "Equals AI routing commands must apply to the rest of the conversation"
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
        actions = [_validate_ha_action(item) for item in actions_value]
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


def canonical_action_signature(actions: Sequence[Mapping[str, Any]]) -> str:
    """Stable action identity for future Suggested Local Commands comparisons."""
    import json

    normalized = [
        {
            "domain": action["domain"],
            "service": action["service"],
            "target": action.get("target", {}),
            "data": action.get("data", {}),
        }
        for action in actions
    ]
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
) -> RuleEvaluation | None:
    """Match and apply local side effects or model-routing state."""
    match = rules.match(text)
    if match is None:
        return None
    rule = match.rule
    action = rule["action"]
    if rule["action_type"] == "local_action":
        try:
            await async_execute_ha_actions(hass, action["actions"])
        except Exception:
            _LOGGER.exception("Request Rule local action failed for %s", rule["id"])
            return RuleEvaluation(match, True, action["failure_response"])
        return RuleEvaluation(match, True, action["success_response"])

    if action["reset"]:
        runtime.reset(session_id)
        return RuleEvaluation(
            match, rule["match_type"] == "equals", action["success_response"]
        )
    override = {}
    if action["model"]:
        override[CONF_CHAT_MODEL] = action["model"]
    if action["reasoning_effort"]:
        selected_model = (
            action["model"]
            or runtime.get(session_id).get(CONF_CHAT_MODEL)
            or configured_model
        )
        if not get_model_config(selected_model).get("supports_reasoning_effort"):
            raise HomeAssistantError(
                f"Model {selected_model} does not support reasoning effort"
            )
        override[CONF_REASONING_EFFORT] = action["reasoning_effort"]
    combined_override = {**runtime.get(session_id), **override}
    combined_model = combined_override.get(CONF_CHAT_MODEL)
    if (
        combined_model
        and combined_override.get(CONF_REASONING_EFFORT)
        and not get_model_config(combined_model).get("supports_reasoning_effort")
    ):
        raise HomeAssistantError(
            f"Model {combined_model} does not support reasoning effort"
        )
    if action["scope"] == "conversation":
        runtime.set(session_id, override)
        request_override = None
    else:
        request_override = override
    consume = rule["match_type"] == "equals"
    return RuleEvaluation(
        match,
        consume,
        action["success_response"] if consume else None,
        request_override,
    )


def normalize_text(text: str, settings: Mapping[str, Any]) -> str:
    """Apply deterministic, conservative speech-text normalization."""
    value = unicodedata.normalize("NFKC", str(text)).casefold()
    value = value.replace("\u2019", "'").replace("-", " ")
    value = re.sub(r"[^\w\s']+", " ", value, flags=re.UNICODE)
    value = re.sub(r"\s+", " ", value).strip()
    if settings.get("wording_alternatives"):
        padded = f" {value} "
        for alternative, canonical in WORDING_ALTERNATIVES:
            padded = padded.replace(f" {alternative} ", f" {canonical} ")
        value = padded.strip()
    if settings.get("word_forms"):
        value = " ".join(_singularize(token) for token in value.split())
    return value


def _singularize(token: str) -> str:
    if len(token) <= 3 or token.endswith(("ss", "us", "is")):
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
