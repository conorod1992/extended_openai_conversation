"""Fast local Request Rules for conversation routing."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from hashlib import sha256
import json
import logging
import re
from time import monotonic
from typing import Any, cast
import unicodedata
from uuid import uuid4

from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.script import Script, async_validate_actions_config
from homeassistant.helpers.storage import Store

from .const import (
    CONF_CHAT_MODEL,
    CONF_REASONING_EFFORT,
    DEFAULT_CHAT_MODEL,
    DEFAULT_CONVERSATION_TIMEOUT_MINUTES,
    DOMAIN,
    SERVICE_CALL_FUNCTION,
)
from .guest_mode import (
    GUEST_MODE_UNAVAILABLE,
    GuestCapabilityPolicy,
    GuestModeDenied,
    guest_arguments_allowed_runtime,
)
from .helpers import get_model_config, get_reasoning_effort_options
from .request_rule_patterns import (
    MAX_AGENT_PATTERN_STATES,
    CompiledSentencePattern,
    MatchBudget,
    PreparedSentenceText,
    SentenceMatchLimitError,
    SentencePatternError,
    compile_sentence_pattern,
    prepare_match_text,
    sentence_capture_names,
    validate_match_input,
)

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 4
STORAGE_KEY_PREFIX = "extended_openai_conversation_responses.request_rules"
MAX_RULES = 500
MAX_PHRASES = 25
MAX_ACTIONS = 20
MAX_SCRIPT_NODES = 500
MAX_SCRIPT_DEPTH = 12
MAX_RULE_NAME_LENGTH = 120
MATCH_TYPES = ("equals", "starts_with", "ends_with", "contains", "sentence_pattern")
ACTION_TYPES = ("local_action", "model_routing")
SLOT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
SLOT_REFERENCE = re.compile(r"(?<!\{)\{([A-Za-z_][A-Za-z0-9_]{0,63})\}(?!\})")
JINJA_SLOT_REFERENCE = re.compile(
    r"\{\{\s*(?:request\.slots\.)?([A-Za-z_][A-Za-z0-9_]{0,63})\s*\}\}"
)
ROUTING_SCOPES = ("request", "conversation")
_REQUEST_RESET_SENTINEL = "__request_rule_reset__"
DEFAULT_MATCHING = {
    "word_forms": True,
    "wording_alternatives": True,
    "fuzzy": False,
    "fuzzy_threshold": 90,
}

# Phrase mappings are deliberately small and directional. Both sides normalize to
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
RequestRuleFunctionExecutor = Callable[[str, dict[str, Any]], Awaitable[Any]]
_ACTIVE_FUNCTION_EXECUTOR: ContextVar[RequestRuleFunctionExecutor | None] = ContextVar(
    "request_rule_function_executor", default=None
)


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
    """One normalized phrase or compiled ExtendedOpenAI sentence pattern."""

    original: str
    normalized: str | None = None
    sentence_pattern: CompiledSentencePattern | None = None


_MATCH_RANK = {
    "equals": 5,
    "sentence_pattern": 4,
    "starts_with": 3,
    "ends_with": 2,
    "contains": 1,
}


@dataclass(frozen=True, slots=True)
class _MatchingSnapshot:
    """One privately owned generation, published only after compilation finishes."""

    phrases: tuple[tuple[dict[str, Any], dict[str, Any], CompiledPhrase], ...]
    wording_groups: tuple[dict[str, Any], ...]
    deterministic: tuple[
        tuple[dict[str, Any], dict[str, Any], CompiledPhrase], ...
    ] = ()


def _match_compiled_sentence(
    compiled: CompiledPhrase,
    prepared: PreparedSentenceText,
    budget: MatchBudget,
) -> dict[str, str] | None:
    """Match one compiled sentence pattern with the shared request budget."""
    if compiled.sentence_pattern is None:
        return None
    result = compiled.sentence_pattern.match_prepared(prepared, budget)
    return None if result is None else dict(result.captures)


@dataclass(frozen=True, slots=True)
class RuleEvaluation:
    """Result of evaluating one utterance."""

    match: RuleMatch
    consume: bool
    response: str | None = None
    request_override: dict[str, str] | None = None
    successful: bool = True


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
        if old_major_version in {2, 3}:
            return old_data
        raise NotImplementedError


def _normalize_legacy_consumed_request_scope(value: Any) -> tuple[Any, bool]:
    """Preserve complete routing commands saved with now-meaningless request scope."""
    if not isinstance(value, Mapping):
        return value, False
    if value.get("action_type", "local_action") != "model_routing":
        return value, False
    if value.get("match_type", "equals") not in {"equals", "sentence_pattern"}:
        return value, False
    action = value.get("action")
    if not isinstance(action, Mapping) or action.get("scope", "request") != "request":
        return value, False
    normalized = deepcopy(dict(value))
    normalized["action"] = {**dict(action), "scope": "conversation"}
    return normalized, True


class RequestRules:
    """Concurrency-safe persisted rules with precomputed matcher state."""

    def __init__(self, store: RequestRuleStore) -> None:
        self._store = store
        self._rules: list[dict[str, Any]] = []
        self._defaults = dict(DEFAULT_MATCHING)
        self._wording_groups = _copy_wording_groups(DEFAULT_WORDING_GROUPS)
        self._matching_snapshot = _MatchingSnapshot((), ())
        self._diagnostics: dict[str, str] = {}
        self._lock = asyncio.Lock()
        self._initialized = False

    async def async_initialize(self) -> None:
        """Load stored rules while preserving newly unsupported patterns for repair."""
        async with self._lock:
            if self._initialized:
                return
            stored = await self._store.async_load()
            migrated = False
            raw_rules: Sequence[Any] = ()
            if stored is None:
                pass
            elif not isinstance(stored, Mapping):
                _LOGGER.warning("Resetting malformed stored Request Rules container")
                migrated = True
            else:
                try:
                    self._defaults = validate_matching_settings(
                        stored.get("defaults", DEFAULT_MATCHING)
                    )
                except ValueError:
                    _LOGGER.warning("Ignoring invalid stored Request Rule defaults")
                    migrated = True
                try:
                    self._wording_groups = validate_wording_groups(
                        stored.get("wording_groups", DEFAULT_WORDING_GROUPS)
                    )
                except ValueError:
                    _LOGGER.warning(
                        "Ignoring invalid stored Request Rule wording groups"
                    )
                    migrated = True
                stored_rules = stored.get("rules", [])
                if not isinstance(stored_rules, Sequence) or isinstance(
                    stored_rules, (str, bytes)
                ):
                    _LOGGER.warning("Resetting malformed stored Request Rules list")
                    migrated = True
                else:
                    if len(stored_rules) > MAX_RULES:
                        _LOGGER.warning(
                            "Stored Request Rules exceed the supported limit; "
                            "keeping the first %d",
                            MAX_RULES,
                        )
                        migrated = True
                    raw_rules = stored_rules[:MAX_RULES]

            seen_ids: set[str] = set()
            for raw in raw_rules:
                try:
                    candidate, scope_migrated = (
                        _normalize_legacy_consumed_request_scope(raw)
                    )
                    if scope_migrated:
                        _LOGGER.warning(
                            "Migrating stored complete Request Rule %s from request "
                            "scope to conversation scope",
                            raw.get("id", "<unknown>")
                            if isinstance(raw, Mapping)
                            else "<unknown>",
                        )
                        migrated = True
                    validated = validate_rule(
                        candidate, validate_sentence_pattern=False
                    )
                    if validated["id"] in seen_ids:
                        _LOGGER.warning(
                            "Ignoring duplicate stored Request Rule id: %s",
                            validated["id"],
                        )
                        migrated = True
                        continue
                    seen_ids.add(validated["id"])
                    self._rules.append(validated)
                    migrated = migrated or validated != raw
                except ValueError as err:
                    _LOGGER.warning("Ignoring invalid stored Request Rule: %s", err)
                    migrated = True
            migrated = self._sort_and_compile() or migrated
            if migrated:
                await self._async_save_locked()
            self._initialized = True

    def revision(self) -> str:
        """Return a deterministic token for the current durable rule set."""
        payload = json.dumps(
            {
                "defaults": self._defaults,
                "wording_groups": self._wording_groups,
                "rules": self._rules,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return sha256(payload.encode("utf-8")).hexdigest()

    def _require_revision_locked(self, expected_revision: str | None) -> None:
        """Reject a stale management writer while the mutation lock is held."""
        if expected_revision is None:
            return
        if not isinstance(expected_revision, str):
            raise ValueError("revision must be a string")
        if expected_revision != self.revision():
            raise ValueError(
                "Request Rules changed in another tab. Reload the latest rules before saving."
            )

    def snapshot(self) -> dict[str, Any]:
        """Return a copy suitable for the management API."""
        return {
            "storage_version": STORAGE_VERSION,
            "revision": self.revision(),
            "defaults": dict(self._defaults),
            "wording_groups": _copy_wording_groups(self._wording_groups),
            "rules": [dict(rule) for rule in self._rules],
            "diagnostics": dict(self._diagnostics),
        }

    def function_references(self, function_name: str) -> list[dict[str, str]]:
        """Return Request Rules that directly call one configured Function Tool."""
        service_action = f"{DOMAIN}.{SERVICE_CALL_FUNCTION}"
        references: list[dict[str, str]] = []
        for rule in self._rules:
            actions = rule.get("action", {}).get("actions", [])
            if any(
                isinstance(action, Mapping)
                and action.get("action", action.get("service")) == service_action
                and isinstance(action.get("data"), Mapping)
                and action["data"].get("function") == function_name
                for action in actions
            ):
                references.append({"id": rule["id"], "name": rule["name"]})
        return references

    async def async_rename_function_reference(
        self,
        old_name: str,
        new_name: str,
        *,
        expected_revision: str | None = None,
    ) -> int:
        """Rewrite exact configured-function references and persist once."""
        if old_name == new_name:
            return 0
        service_action = f"{DOMAIN}.{SERVICE_CALL_FUNCTION}"
        async with self._lock:
            self._require_revision_locked(expected_revision)
            changed = 0
            updated_rules: list[dict[str, Any]] = []
            for rule in self._rules:
                updated = deepcopy(rule)
                for action in updated.get("action", {}).get("actions", []):
                    if (
                        isinstance(action, dict)
                        and action.get("action", action.get("service"))
                        == service_action
                        and isinstance(action.get("data"), Mapping)
                        and action["data"].get("function") == old_name
                    ):
                        action["data"] = {**action["data"], "function": new_name}
                        changed += 1
                updated_rules.append(
                    validate_rule(
                        updated,
                        validate_sentence_pattern=rule["id"] not in self._diagnostics,
                    )
                )
            if changed:
                _validate_total_pattern_states(
                    updated_rules, inactive_rule_ids=self._diagnostics
                )
                self._rules = updated_rules
                self._sort_and_compile()
                await self._async_save_locked()
        return changed

    async def async_backup_data(self) -> dict[str, Any]:
        """Return durable Request Rule state without management-only fields."""
        snapshot = self.snapshot()
        snapshot.pop("revision", None)
        snapshot.pop("diagnostics", None)
        return snapshot

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
        rules = []
        for item in raw_rules:
            candidate, _ = _normalize_legacy_consumed_request_scope(item)
            rules.append(validate_rule(candidate, validate_sentence_pattern=False))
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

    async def async_set_defaults(
        self, value: Any, *, expected_revision: str | None = None
    ) -> dict[str, Any]:
        """Replace global matching defaults."""
        defaults = validate_matching_settings(value)
        async with self._lock:
            self._require_revision_locked(expected_revision)
            self._defaults = defaults
            self._sort_and_compile()
            await self._async_save_locked()
        return dict(defaults)

    async def async_set_wording_groups(
        self, value: Any, *, expected_revision: str | None = None
    ) -> list[dict[str, Any]]:
        """Replace the persisted wording synonym groups."""
        groups = validate_wording_groups(value)
        async with self._lock:
            self._require_revision_locked(expected_revision)
            self._wording_groups = groups
            self._sort_and_compile()
            await self._async_save_locked()
        return _copy_wording_groups(groups)

    async def async_create(
        self, value: Any, *, expected_revision: str | None = None
    ) -> dict[str, Any]:
        """Create one rule."""
        if not isinstance(value, Mapping):
            raise ValueError("rule must be an object")
        async with self._lock:
            self._require_revision_locked(expected_revision)
            if len(self._rules) >= MAX_RULES:
                raise ValueError("Request Rule limit reached")
            raw = dict(value)
            raw.setdefault("id", uuid4().hex)
            raw.setdefault("order", len(self._rules))
            rule = validate_rule(raw)
            if any(item["id"] == rule["id"] for item in self._rules):
                raise ValueError("rule id already exists")
            _validate_total_pattern_states(
                [*self._rules, rule], inactive_rule_ids=self._diagnostics
            )
            self._rules.append(rule)
            self._sort_and_compile()
            await self._async_save_locked()
        return dict(rule)

    async def async_update(
        self,
        rule_id: str,
        value: Any,
        *,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        """Replace one rule while preserving its id."""
        if not isinstance(value, Mapping):
            raise ValueError("rule must be an object")
        async with self._lock:
            self._require_revision_locked(expected_revision)
            index = self._index(rule_id)
            raw = dict(value)
            raw["id"] = rule_id
            raw.setdefault("order", self._rules[index]["order"])
            previous = self._rules[index]
            preserve_inactive = (
                rule_id in self._diagnostics
                and raw.get("phrases") == previous["phrases"]
                and raw.get("match_type", "equals") == previous["match_type"]
                and not raw.get("enabled", True)
            )
            rule = validate_rule(raw, validate_sentence_pattern=not preserve_inactive)
            prospective = [*self._rules]
            prospective[index] = rule
            _validate_total_pattern_states(
                prospective, inactive_rule_ids=set(self._diagnostics) - {rule_id}
            )
            self._rules[index] = rule
            self._sort_and_compile()
            await self._async_save_locked()
        return dict(rule)

    async def async_delete(
        self, rule_id: str, *, expected_revision: str | None = None
    ) -> bool:
        """Delete one rule."""
        async with self._lock:
            self._require_revision_locked(expected_revision)
            index = self._index(rule_id)
            del self._rules[index]
            self._sort_and_compile()
            await self._async_save_locked()
        return True

    async def async_duplicate(
        self, rule_id: str, *, expected_revision: str | None = None
    ) -> dict[str, Any]:
        """Duplicate one rule immediately after its source."""
        async with self._lock:
            self._require_revision_locked(expected_revision)
            if len(self._rules) >= MAX_RULES:
                raise ValueError("Request Rule limit reached")
            source_index = self._index(rule_id)
            source = deepcopy(self._rules[source_index])
            source.update(
                id=uuid4().hex,
                name=_duplicate_rule_name(source["name"], self._rules),
                order=source_index + 1,
            )
            rule = validate_rule(source)
            prospective = [*self._rules]
            prospective.insert(source_index + 1, rule)
            for order, item in enumerate(prospective):
                item["order"] = order
            _validate_total_pattern_states(
                prospective, inactive_rule_ids=self._diagnostics
            )
            self._rules = prospective
            self._sort_and_compile()
            await self._async_save_locked()
        return dict(rule)

    async def async_move(
        self,
        rule_id: str,
        direction: str,
        *,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        """Move one rule by one position while preserving matching precedence."""
        if direction not in {"up", "down"}:
            raise ValueError("direction must be up or down")
        async with self._lock:
            self._require_revision_locked(expected_revision)
            index = self._index(rule_id)
            target = index - 1 if direction == "up" else index + 1
            if target < 0 or target >= len(self._rules):
                return dict(self._rules[index])
            self._rules[index], self._rules[target] = (
                self._rules[target],
                self._rules[index],
            )
            for order, rule in enumerate(self._rules):
                rule["order"] = order
            self._sort_and_compile()
            await self._async_save_locked()
            return dict(self._rules[target])

    def match(self, text: str) -> RuleMatch | None:
        """Use one generation and existing precedence, with fuzzy only as fallback."""
        snapshot = self._matching_snapshot
        validate_match_input(text)
        normalized_candidates: dict[tuple[bool, bool], str] = {}
        sentence_text: PreparedSentenceText | None = None
        sentence_budget = MatchBudget()

        def candidate(settings: dict[str, Any]) -> str:
            key = (
                bool(settings.get("word_forms")),
                bool(settings.get("wording_alternatives")),
            )
            if key not in normalized_candidates:
                normalized_candidates[key] = normalize_text(
                    text, settings, snapshot.wording_groups
                )
            return normalized_candidates[key]

        # Published phrases are already sorted by the original deterministic
        # ranking. Once one succeeds, no unvisited phrase can change the winner.
        for rule, settings, compiled in snapshot.deterministic:
            if compiled.sentence_pattern is not None:
                if sentence_text is None:
                    sentence_text = prepare_match_text(text)
                slots = _match_compiled_sentence(
                    compiled, sentence_text, sentence_budget
                )
                if slots is not None:
                    return RuleMatch(rule, compiled.original, False, 100.0, slots)
            elif _deterministic_match(
                candidate(settings), cast(str, compiled.normalized), rule["match_type"]
            ):
                return RuleMatch(rule, compiled.original, False, 100.0)

        # Fuzzy ties historically use rule order, then phrase order, rather than
        # phrase length. Restore that order after the deterministic pass.
        fuzzy: list[tuple[tuple[float, int, int], RuleMatch]] = []
        for rule, settings, compiled in snapshot.phrases:
            if compiled.sentence_pattern is not None or not settings["fuzzy"]:
                continue
            score = _fuzzy_score(
                candidate(settings), cast(str, compiled.normalized), rule["match_type"]
            )
            if score >= settings["fuzzy_threshold"]:
                result = RuleMatch(rule, compiled.original, True, score)
                fuzzy.append(
                    ((score, _MATCH_RANK[rule["match_type"]], -rule["order"]), result)
                )
        return max(fuzzy, key=lambda item: item[0])[1] if fuzzy else None

    async def async_match(self, hass: HomeAssistant, text: str) -> RuleMatch | None:
        """Run matching off-loop, including when used with lightweight hosts."""
        executor = getattr(hass, "async_add_executor_job", None)
        if callable(executor):
            return cast(RuleMatch | None, await executor(self.match, text))
        return await asyncio.to_thread(self.match, text)

    def _index(self, rule_id: str) -> int:
        for index, rule in enumerate(self._rules):
            if rule["id"] == rule_id:
                return index
        raise ValueError("Request Rule not found")

    def _sort_and_compile(self) -> bool:
        """Sort, reindex, compile safe rules, and retain diagnostics for unsafe ones."""
        self._rules.sort(
            key=lambda item: (
                item["order"],
                item["name"].casefold(),
                item["id"],
            )
        )
        order_changed = False
        for index, rule in enumerate(self._rules):
            if rule["order"] != index:
                rule["order"] = index
                order_changed = True

        compiled_rules: list[tuple[dict[str, Any], dict[str, Any], CompiledPhrase]] = []
        diagnostics: dict[str, str] = {}
        total_pattern_states = 0
        for stored_rule in self._rules:
            rule = deepcopy(stored_rule)
            settings = (
                self._defaults
                if rule["matching_behavior"] == "defaults"
                else rule["matching"]
            )
            if rule["match_type"] == "sentence_pattern":
                try:
                    phrases = [
                        _compile_sentence_pattern(item) for item in rule["phrases"]
                    ]
                    phrase_slots = [
                        set(
                            cast(
                                CompiledSentencePattern, item.sentence_pattern
                            ).capture_names
                        )
                        for item in phrases
                    ]
                    if any(names != phrase_slots[0] for names in phrase_slots[1:]):
                        raise ValueError(
                            "all sentence variants must capture the same slots"
                        )
                    state_count = sum(
                        cast(CompiledSentencePattern, item.sentence_pattern).state_count
                        for item in phrases
                    )
                    if rule["enabled"] and (
                        total_pattern_states + state_count > MAX_AGENT_PATTERN_STATES
                    ):
                        raise ValueError(
                            "enabled sentence patterns exceed the per-agent compiled "
                            f"state limit of {MAX_AGENT_PATTERN_STATES}"
                        )
                except ValueError as err:
                    diagnostic = f"Sentence pattern is inactive: {err}"
                    diagnostics[rule["id"]] = diagnostic
                    _LOGGER.warning("Request Rule %s is inactive: %s", rule["id"], err)
                    continue
                if rule["enabled"]:
                    total_pattern_states += state_count
            else:
                phrases = [
                    CompiledPhrase(
                        item, normalize_text(item, settings, self._wording_groups)
                    )
                    for item in rule["phrases"]
                ]

            if rule["enabled"]:
                compiled_rules.extend(
                    (rule, dict(settings), phrase) for phrase in phrases
                )
        deterministic = sorted(
            compiled_rules,
            key=lambda item: (
                _MATCH_RANK[item[0]["match_type"]],
                len(
                    item[2].original
                    if item[2].sentence_pattern is not None
                    else cast(str, item[2].normalized)
                ),
                -item[0]["order"],
            ),
            reverse=True,
        )
        self._matching_snapshot = _MatchingSnapshot(
            tuple(compiled_rules),
            tuple(_copy_wording_groups(self._wording_groups)),
            tuple(deterministic),
        )
        self._diagnostics = diagnostics
        return order_changed

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
        request_values = dict(request_override or {})
        reset_request = request_values.pop(_REQUEST_RESET_SENTINEL, None) == "1"
        if reset_request:
            return {**defaults, **request_values}
        return {
            **defaults,
            **self.get(session_id, timeout_minutes),
            **request_values,
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
        if any(not term for term in normalized):
            raise ValueError("wording phrases must contain searchable text")
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


def validate_rule(
    value: Any, *, validate_sentence_pattern: bool = True
) -> dict[str, Any]:
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
    name = _clean(value.get("name"), MAX_RULE_NAME_LENGTH, "name")
    phrases_value = value.get("phrases")
    if not isinstance(phrases_value, Sequence) or isinstance(phrases_value, str):
        raise ValueError("phrases must be a list")
    phrases = list(dict.fromkeys(_clean(item, 200, "phrase") for item in phrases_value))
    if not phrases or len(phrases) > MAX_PHRASES:
        raise ValueError(f"phrases must contain 1 to {MAX_PHRASES} items")
    match_type = value.get("match_type", "equals")
    if match_type not in MATCH_TYPES:
        raise ValueError("unsupported match type")

    sentence_valid = True
    if match_type == "sentence_pattern":
        if validate_sentence_pattern:
            compiled_phrases = [_compile_sentence_pattern(phrase) for phrase in phrases]
            phrase_slots = [
                set(cast(CompiledSentencePattern, item.sentence_pattern).capture_names)
                for item in compiled_phrases
            ]
        else:
            try:
                phrase_slots = [
                    set(sentence_capture_names(phrase)) for phrase in phrases
                ]
            except SentencePatternError:
                sentence_valid = False
                phrase_slots = []
        if phrase_slots and any(names != phrase_slots[0] for names in phrase_slots[1:]):
            if validate_sentence_pattern:
                raise ValueError("all sentence variants must capture the same slots")
            sentence_valid = False
        if sentence_valid and phrase_slots:
            slot_names = sorted(phrase_slots[0])
        else:
            slot_names = _stored_slot_names(value)
    else:
        slot_names = []
        if any(SLOT_REFERENCE.search(phrase) for phrase in phrases):
            raise ValueError("variable values require Sentence pattern matching")

    action_type = value.get("action_type", "local_action")
    if action_type not in ACTION_TYPES:
        raise ValueError("unsupported action type")
    raw_action = value.get("action", {})
    action = _validate_action(action_type, raw_action)
    referenced_slots = _referenced_slots(action) | _legacy_action_slots(raw_action)
    unknown_slots = referenced_slots - set(slot_names)
    if unknown_slots and (validate_sentence_pattern or sentence_valid):
        raise ValueError("unknown captured value: " + ", ".join(sorted(unknown_slots)))
    if (
        action_type == "model_routing"
        and match_type in {"equals", "sentence_pattern"}
        and action["scope"] == "request"
    ):
        raise ValueError(
            "Equals and Sentence pattern AI routing commands are consumed locally; "
            "use the rest of the conversation scope"
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
        "slots": [{"name": item} for item in slot_names],
    }


def _stored_slot_names(value: Mapping[str, Any]) -> list[str]:
    """Recover prior capture metadata when a stored legacy pattern cannot parse."""
    result: list[str] = []
    raw_slots = value.get("slots", [])
    if not isinstance(raw_slots, Sequence) or isinstance(raw_slots, (str, bytes)):
        return result
    for slot in raw_slots:
        if (
            isinstance(slot, Mapping)
            and isinstance(slot.get("name"), str)
            and SLOT_NAME.fullmatch(str(slot["name"]))
        ):
            result.append(str(slot["name"]))
    return sorted(set(result))


def _validate_total_pattern_states(
    rules: Sequence[Mapping[str, Any]], *, inactive_rule_ids: Collection[str] = ()
) -> None:
    """Account in loader order, retaining unchanged inactive rules for repair."""
    total = 0
    for rule in sorted(
        rules, key=lambda item: (item["order"], item["name"].casefold(), item["id"])
    ):
        if not rule.get("enabled") or rule.get("match_type") != "sentence_pattern":
            continue
        phrases = rule.get("phrases", [])
        if not isinstance(phrases, Sequence) or isinstance(phrases, str):
            continue
        try:
            compiled = [compile_sentence_pattern(str(phrase)) for phrase in phrases]
            if any(
                set(item.capture_names) != set(compiled[0].capture_names)
                for item in compiled[1:]
            ):
                raise ValueError("all sentence variants must capture the same slots")
            state_count = sum(item.state_count for item in compiled)
            if total + state_count > MAX_AGENT_PATTERN_STATES:
                raise ValueError(
                    "enabled sentence patterns exceed the per-agent compiled "
                    f"state limit of {MAX_AGENT_PATTERN_STATES}"
                )
        except ValueError:
            if rule.get("id") in inactive_rule_ids:
                continue
            raise
        total += state_count


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
        actions = _validate_script_sequence(actions_value)
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
    for routing_value in (model, effort):
        if any(marker in routing_value for marker in ("{{", "{%", "{#")):
            raise ValueError(
                "model routing captured values must use simple {name} references"
            )
    model_dynamic = bool(SLOT_REFERENCE.search(model))
    effort_dynamic = bool(SLOT_REFERENCE.search(effort))
    if effort_dynamic and SLOT_REFERENCE.fullmatch(effort) is None:
        raise ValueError("captured reasoning effort must be a single {name} reference")
    if effort and not effort_dynamic:
        if model and not model_dynamic:
            if not get_model_config(model).get("supports_reasoning_effort"):
                raise ValueError(f"model {model} does not support reasoning effort")
            if effort not in get_reasoning_effort_options(model):
                raise ValueError(
                    f"reasoning effort {effort} is not supported by model {model}"
                )
        elif effort not in get_reasoning_effort_options("gpt-6-astra"):
            # The effective model may come from the configured/conversation route.
            # Accept every currently supported value here; runtime validates it
            # against that effective model before publishing any route change.
            raise ValueError("unsupported reasoning effort")
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
        "action": f"{domain}.{service}",
        "target": _migrate_slot_templates(dict(value.get("target") or {})),
        "data": _migrate_slot_templates(dict(value.get("data") or {})),
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
    """Migrate one legacy HA or configured-function action to native syntax."""
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
                normalized_arguments[name] = _migrate_slot_templates(binding)
                continue
            source = binding.get("source", "fixed")
            if source == "slot":
                if set(binding) != {"source", "slot"}:
                    raise ValueError("slot arguments need only source and slot")
                slot = binding.get("slot")
                if not isinstance(slot, str) or not SLOT_NAME.fullmatch(slot):
                    raise ValueError("slot argument must name a captured value")
                normalized_arguments[name] = f"{{{{ {slot} }}}}"
            elif source == "fixed":
                if set(binding) - {"source", "value"}:
                    raise ValueError("fixed arguments need only source and value")
                normalized_arguments[name] = _migrate_slot_templates(
                    binding.get("value")
                )
            else:
                raise ValueError("function argument source must be fixed or slot")
        return {
            "action": f"{DOMAIN}.{SERVICE_CALL_FUNCTION}",
            "data": {
                "function": function_name,
                "arguments": normalized_arguments,
            },
        }
    if isinstance(value, Mapping) and value.get("type") == "home_assistant":
        value = {key: item for key, item in value.items() if key != "type"}
    return _validate_ha_action(value)


def _migrate_slot_templates(value: Any) -> Any:
    """Translate legacy braces into Home Assistant script templates."""
    if isinstance(value, str):
        return SLOT_REFERENCE.sub(lambda match: f"{{{{ {match.group(1)} }}}}", value)
    if isinstance(value, Mapping):
        if set(value) == {"value_from", "slot"} and value.get("value_from") == "slot":
            return f"{{{{ {value['slot']} }}}}"
        return {key: _migrate_slot_templates(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_migrate_slot_templates(item) for item in value]
    return value


def _legacy_action_slots(value: Any) -> set[str]:
    """Collect slot references only from recognizably legacy local actions."""
    if not isinstance(value, Mapping):
        return set()
    actions = value.get("actions", [])
    if not isinstance(actions, Sequence) or isinstance(actions, str):
        return set()
    result: set[str] = set()
    for action in actions:
        if not isinstance(action, Mapping) or not (
            "domain" in action or action.get("type") in {"function", "home_assistant"}
        ):
            continue
        result.update(_referenced_slots(action))
    return result


def _validate_script_sequence(value: Sequence[Any]) -> list[dict[str, Any]]:
    """Validate native HA script syntax and enforce conservative size bounds."""
    migrated = [
        _validate_local_action(item)
        if isinstance(item, Mapping)
        and ("domain" in item or item.get("type") in {"function", "home_assistant"})
        else item
        for item in value
    ]
    _validate_script_complexity(migrated)
    try:
        cv.SCRIPT_SCHEMA(_mask_script_templates(migrated))
    except Exception as err:
        raise ValueError(f"invalid Home Assistant action sequence: {err}") from err
    return cast(list[dict[str, Any]], migrated)


def _mask_script_templates(value: Any, *, key: str | None = None) -> Any:
    """Permit context-free schema validation while preserving stored templates."""
    if isinstance(value, str) and ("{{" in value or "{%" in value or "{#" in value):
        return (
            "homeassistant.update_entity"
            if key in {"action", "service"}
            else "request_rule_template"
        )
    if isinstance(value, Mapping):
        return {
            item_key: _mask_script_templates(item, key=str(item_key))
            for item_key, item in value.items()
        }
    if isinstance(value, list):
        return [_mask_script_templates(item) for item in value]
    return value


def _validate_script_complexity(value: Any, *, depth: int = 0) -> int:
    if depth > MAX_SCRIPT_DEPTH:
        raise ValueError(f"action sequence exceeds maximum depth {MAX_SCRIPT_DEPTH}")
    if isinstance(value, Mapping):
        total = 1 + sum(
            _validate_script_complexity(item, depth=depth + 1)
            for item in value.values()
        )
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        total = 1 + sum(
            _validate_script_complexity(item, depth=depth + 1) for item in value
        )
    else:
        total = 1
    if total > MAX_SCRIPT_NODES:
        raise ValueError(f"action sequence exceeds {MAX_SCRIPT_NODES} nodes")
    return total


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

    return json.dumps(actions, sort_keys=True, separators=(",", ":"))


def _iter_script_actions(
    sequence: Sequence[Mapping[str, Any]],
    *,
    depth: int = 0,
    budget: list[int] | None = None,
):
    """Yield executable action mappings from native nested script branches."""
    if depth > MAX_SCRIPT_DEPTH:
        raise ValueError(f"action sequence exceeds maximum depth {MAX_SCRIPT_DEPTH}")
    if budget is None:
        budget = [0]

    def nested_sequence(value: Any):
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
            return None
        if not all(isinstance(child, Mapping) for child in value):
            return None
        return cast(Sequence[Mapping[str, Any]], value)

    for item in sequence:
        budget[0] += 1
        if budget[0] > MAX_SCRIPT_NODES:
            raise ValueError(f"action sequence exceeds {MAX_SCRIPT_NODES} nodes")
        yield item

        for key in ("sequence", "then", "else", "default", "parallel"):
            nested = nested_sequence(item.get(key))
            if nested is not None:
                yield from _iter_script_actions(nested, depth=depth + 1, budget=budget)

        choose = item.get("choose")
        if isinstance(choose, Sequence) and not isinstance(choose, (str, bytes)):
            for branch in choose:
                if not isinstance(branch, Mapping):
                    continue
                nested = nested_sequence(branch.get("sequence"))
                if nested is not None:
                    yield from _iter_script_actions(
                        nested, depth=depth + 1, budget=budget
                    )

        repeat = item.get("repeat")
        if isinstance(repeat, Mapping):
            nested = nested_sequence(repeat.get("sequence"))
            if nested is not None:
                yield from _iter_script_actions(nested, depth=depth + 1, budget=budget)


def rule_has_sensitive_actions(rule: Mapping[str, Any]) -> bool:
    """Flag tolerant matching for sensitive actions anywhere in a script tree."""
    if rule.get("action_type") != "local_action":
        return False
    actions = cast(Mapping[str, Any], rule.get("action", {})).get("actions", [])
    for action in _iter_script_actions(cast(Sequence[Mapping[str, Any]], actions)):
        service_name = str(action.get("action", action.get("service", "")))
        domain, _, service = service_name.casefold().partition(".")
        if domain in SENSITIVE_DOMAINS or (
            domain == "cover" and any(term in service for term in ("open", "close"))
        ):
            return True
    return False


async def async_call_active_function(function: str, arguments: Any) -> Any:
    """Execute an integration function in the active Request Rule context."""
    executor = _ACTIVE_FUNCTION_EXECUTOR.get()
    if executor is None:
        raise HomeAssistantError(
            "This action is only available while a Request Rule is running"
        )
    if not isinstance(arguments, Mapping):
        raise HomeAssistantError("Function arguments must be an object")
    return await executor(function, dict(arguments))


def _resolve_guest_slot_templates(value: Any, slots: Mapping[str, str]) -> Any:
    """Resolve only deterministic captured-value templates for Guest preflight."""

    def replace_slot(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in slots:
            raise GuestModeDenied(GUEST_MODE_UNAVAILABLE)
        return slots[name]

    if isinstance(value, str):
        rendered = JINJA_SLOT_REFERENCE.sub(replace_slot, value)
        if "{{" in rendered or "{%" in rendered or "{#" in rendered:
            raise GuestModeDenied(GUEST_MODE_UNAVAILABLE)
        return rendered
    if isinstance(value, Mapping):
        return {
            key: _resolve_guest_slot_templates(item, slots)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_resolve_guest_slot_templates(item, slots) for item in value]
    return value


def _guest_script_allowed(
    hass: HomeAssistant,
    sequence: Sequence[Mapping[str, Any]],
    policy: GuestCapabilityPolicy,
) -> bool:
    """Preauthorize every executable action before a Guest script can start."""
    for item in _iter_script_actions(sequence):
        action_name = item.get("action", item.get("service"))
        if action_name is not None:
            if not isinstance(action_name, str):
                return False
            if action_name == f"{DOMAIN}.{SERVICE_CALL_FUNCTION}":
                data = item.get("data", {})
                if not isinstance(data, Mapping):
                    return False
                function_name = data.get("function")
                if not isinstance(
                    function_name, str
                ) or not policy.allows_configured_tool(function_name):
                    return False
                continue
            if not guest_arguments_allowed_runtime(
                hass,
                item,
                policy,
                control=True,
                require_entity_selector=True,
            ):
                return False
        elif any(key in item for key in ("device_id", "event", "event_type")):
            # Device and event actions do not provide an entity-scoped boundary.
            return False
    return True


def _resolved_routing_value(value: str, slots: Mapping[str, str], field: str) -> str:
    """Resolve one deterministic captured routing value and reject empty results."""
    resolved = resolve_slot_values(value, slots)
    if not isinstance(resolved, str) or not resolved.strip():
        raise HomeAssistantError(f"Captured routing {field} is empty")
    return resolved.strip()


def _validate_effective_reasoning(
    model: str, effort: str, *, captured: bool = False
) -> None:
    """Validate a resolved reasoning value against the model that will receive it."""
    if not get_model_config(model).get("supports_reasoning_effort"):
        raise HomeAssistantError(f"Model {model} does not support reasoning effort")
    if effort not in get_reasoning_effort_options(model):
        if captured:
            raise HomeAssistantError(f"Unsupported captured reasoning effort: {effort}")
        raise HomeAssistantError(
            f"Reasoning effort {effort} is not supported by model {model}"
        )


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
    context: Context | None = None,
) -> RuleEvaluation | None:
    """Match and apply local side effects or model-routing state."""
    try:
        match = await rules.async_match(hass, text)
    except SentenceMatchLimitError as err:
        _LOGGER.warning("Skipping Request Rules for bounded matching failure: %s", err)
        return None
    if match is None:
        return None
    rule = match.rule
    action = rule["action"]
    if rule["action_type"] == "local_action":
        policy = guest_policy or GuestCapabilityPolicy.unrestricted()
        executable_actions = action["actions"]
        if policy.guest_active:
            try:
                executable_actions = _resolve_guest_slot_templates(
                    executable_actions, match.slots
                )
                allowed = _guest_script_allowed(
                    hass,
                    cast(Sequence[Mapping[str, Any]], executable_actions),
                    policy,
                )
            except GuestModeDenied:
                allowed = False
            except Exception:
                _LOGGER.exception(
                    "Guest authorization failed for Request Rule %s", rule["id"]
                )
                allowed = False
            if not allowed:
                return RuleEvaluation(
                    match, True, GUEST_MODE_UNAVAILABLE, successful=False
                )
        try:
            schema_actions = cv.SCRIPT_SCHEMA(executable_actions)
            validated_actions = await async_validate_actions_config(
                hass, schema_actions
            )
            script = Script(
                hass,
                validated_actions,
                f"Request Rule {rule['id']}",
                DOMAIN,
                log_exceptions=False,
            )
            token = _ACTIVE_FUNCTION_EXECUTOR.set(function_executor)
            try:
                await script.async_run(
                    {
                        **match.slots,
                        "request": {"slots": dict(match.slots)},
                    },
                    context,
                )
            finally:
                _ACTIVE_FUNCTION_EXECUTOR.reset(token)
                await script.async_unload()
        except GuestModeDenied:
            return RuleEvaluation(match, True, GUEST_MODE_UNAVAILABLE, successful=False)
        except Exception:
            _LOGGER.exception("Request Rule local action failed for %s", rule["id"])
            return RuleEvaluation(
                match,
                True,
                resolve_slot_values(action["failure_response"], match.slots),
                successful=False,
            )
        return RuleEvaluation(
            match, True, resolve_slot_values(action["success_response"], match.slots)
        )

    if action["reset"]:
        if action["scope"] == "conversation":
            runtime.reset(session_id)
            request_override = None
        else:
            request_override = {_REQUEST_RESET_SENTINEL: "1"}
        return RuleEvaluation(
            match,
            rule["match_type"] in {"equals", "sentence_pattern"},
            resolve_slot_values(action["success_response"], match.slots),
            request_override,
        )

    model = (
        _resolved_routing_value(action["model"], match.slots, "model")
        if action["model"]
        else None
    )
    effort = (
        _resolved_routing_value(
            action["reasoning_effort"], match.slots, "reasoning effort"
        )
        if action["reasoning_effort"]
        else None
    )
    conversation_override = runtime.get(session_id, timeout_minutes)
    selected_model = (
        model or conversation_override.get(CONF_CHAT_MODEL) or configured_model
    )
    if effort:
        captured_effort = bool(
            action["reasoning_effort"]
            and SLOT_REFERENCE.fullmatch(action["reasoning_effort"])
        )
        captured_model = bool(
            action["model"] and SLOT_REFERENCE.search(action["model"])
        )
        _validate_effective_reasoning(
            selected_model,
            effort,
            captured=captured_effort and not captured_model,
        )

    override = {}
    if model:
        override[CONF_CHAT_MODEL] = model
    if effort:
        override[CONF_REASONING_EFFORT] = effort
    combined_override = {**conversation_override, **override}
    combined_model = combined_override.get(CONF_CHAT_MODEL, configured_model)
    combined_effort = combined_override.get(CONF_REASONING_EFFORT)
    if combined_effort:
        _validate_effective_reasoning(combined_model, combined_effort)
    if action["scope"] == "conversation":
        runtime.set(session_id, override, timeout_minutes)
        request_override = None
    else:
        request_override = override
    consume = rule["match_type"] in {"equals", "sentence_pattern"}
    return RuleEvaluation(
        match,
        consume,
        (
            resolve_slot_values(action["success_response"], match.slots)
            if consume
            else None
        ),
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
    """Compile the documented ExtendedOpenAI sentence-pattern syntax."""
    try:
        sentence_pattern = compile_sentence_pattern(pattern)
    except SentencePatternError as err:
        raise ValueError(f"invalid sentence pattern: {err}") from err
    return CompiledPhrase(pattern, sentence_pattern=sentence_pattern)


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


def _duplicate_rule_name(source_name: str, rules: Sequence[Mapping[str, Any]]) -> str:
    """Return a unique duplicate name without exceeding the persisted limit."""
    existing = {str(rule.get("name", "")).casefold() for rule in rules}
    number = 1
    while True:
        suffix = " copy" if number == 1 else f" copy {number}"
        base = source_name[: MAX_RULE_NAME_LENGTH - len(suffix)].rstrip()
        candidate = f"{base}{suffix}"
        if candidate.casefold() not in existing:
            return candidate
        number += 1


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
