"""Deterministic, locally enforced protection for Home Assistant actions."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from contextvars import ContextVar, Token
from dataclasses import dataclass
import hashlib
import hmac
import logging
import re
import secrets
from time import monotonic
from typing import Any
from uuid import uuid4

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import target as target_helpers
from homeassistant.helpers.storage import Store

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
STORAGE_KEY_PREFIX = f"{DOMAIN}.protected_actions"
PROTECTION_NONE = "none"
PROTECTION_CONFIRMATION = "confirmation"
PROTECTION_PIN = "pin"
PROTECTION_LEVELS = (PROTECTION_CONFIRMATION, PROTECTION_PIN)
CHALLENGE_TTL_SECONDS = 120
MAX_PIN_ATTEMPTS = 3
PIN_COOLDOWN_SECONDS = 60
PBKDF2_ITERATIONS = 310_000
MAX_RULES = 200
MAX_PIN_SOURCES = 512

CONFIRM_PHRASES = frozenset({"yes", "confirm", "go ahead", "do it"})
CANCEL_PHRASES = frozenset({"no", "cancel", "never mind"})
_DIGIT_WORDS = {
    "zero": "0",
    "oh": "0",
    "o": "0",
    "one": "1",
    "won": "1",
    "two": "2",
    "too": "2",
    "to": "2",
    "three": "3",
    "four": "4",
    "for": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "ate": "8",
    "nine": "9",
}


@dataclass(frozen=True, slots=True)
class ProtectionContext:
    """Identity binding for one protected-action conversation."""

    entry_id: str
    subentry_id: str
    conversation_id: str
    user_id: str | None = None
    device_id: str | None = None
    satellite_id: str | None = None

    @property
    def key(self) -> tuple[str, ...]:
        return (
            self.entry_id,
            self.subentry_id,
            self.conversation_id,
            self.user_id or "",
            self.device_id or "",
            self.satellite_id or "",
        )

    @property
    def source_key(self) -> tuple[str, ...]:
        """Stable trusted source identity used only for PIN rate limiting."""
        return (
            self.entry_id,
            self.subentry_id,
            self.user_id or "",
            self.device_id or "",
            self.satellite_id or "",
        )


@dataclass(frozen=True, slots=True)
class ActiveProtection:
    manager: ProtectedActions
    context: ProtectionContext


@dataclass(slots=True)
class PendingChallenge:
    context: ProtectionContext
    actions: list[dict[str, Any]]
    protection: str
    rule_id: str
    rule_name: str
    expires_at: float
    failed_attempts: int = 0


@dataclass(frozen=True, slots=True)
class ChallengeReply:
    handled: bool
    response: str = ""
    actions: tuple[dict[str, Any], ...] = ()
    redact_input: bool = False


class ProtectedActionRequired(HomeAssistantError):
    """Raised before a protected action so the conversation can challenge locally."""

    def __init__(self, prompt: str) -> None:
        super().__init__(prompt)
        self.prompt = prompt


_ACTIVE_PROTECTION: ContextVar[ActiveProtection | None] = ContextVar(
    "extended_openai_active_protection", default=None
)
_BYPASS_PROTECTION: ContextVar[bool] = ContextVar(
    "extended_openai_bypass_protection", default=False
)


def set_active_protection(
    manager: ProtectedActions, context: ProtectionContext
) -> Token[ActiveProtection | None]:
    """Bind action calls in the current async request to an agent and identity."""
    return _ACTIVE_PROTECTION.set(ActiveProtection(manager, context))


def reset_active_protection(token: Token[ActiveProtection | None]) -> None:
    _ACTIVE_PROTECTION.reset(token)


def protection_bypassed() -> bool:
    return _BYPASS_PROTECTION.get()


def set_protection_bypass() -> Token[bool]:
    return _BYPASS_PROTECTION.set(True)


def reset_protection_bypass(token: Token[bool]) -> None:
    _BYPASS_PROTECTION.reset(token)


async def async_require_protection(actions: Sequence[Mapping[str, Any]]) -> None:
    """Preflight actions through the active agent policy, if any."""
    active = _ACTIVE_PROTECTION.get()
    if active is None or protection_bypassed():
        return
    await active.manager.async_require(active.context, actions)


class ProtectedActionStore(Store[dict[str, Any]]):
    """Private per-agent protected-action storage."""


class ProtectedActions:
    """Persisted protection rules plus ephemeral, fail-closed challenges."""

    def __init__(
        self, store: ProtectedActionStore, hass: HomeAssistant | None = None
    ) -> None:
        self._store = store
        self._hass = hass
        self._rules: list[dict[str, Any]] = []
        self._pin_hash: str | None = None
        self._pending: dict[tuple[str, ...], PendingChallenge] = {}
        self._cooldowns: dict[tuple[str, ...], float] = {}
        self._pin_failures: dict[tuple[str, ...], tuple[int, float]] = {}
        self._lock = asyncio.Lock()
        self._initialized = False

    async def async_initialize(self) -> None:
        async with self._lock:
            if self._initialized:
                return
            stored = await self._store.async_load()
            if isinstance(stored, Mapping):
                pin_hash = stored.get("pin_hash")
                if isinstance(pin_hash, str) and _valid_pin_hash(pin_hash):
                    self._pin_hash = pin_hash
                for raw in stored.get("rules", []):
                    try:
                        self._rules.append(validate_protection_rule(raw))
                    except ValueError as err:
                        _LOGGER.warning(
                            "Ignoring invalid Protected Action rule: %s", err
                        )
            self._sort()
            self._initialized = True

    def snapshot(self) -> dict[str, Any]:
        """Return only frontend-safe policy data."""
        return {
            "storage_version": STORAGE_VERSION,
            "pin_configured": self._pin_hash is not None,
            "rules": [dict(rule) for rule in self._rules],
            "challenge_ttl_seconds": CHALLENGE_TTL_SECONDS,
            "max_pin_attempts": MAX_PIN_ATTEMPTS,
            "pin_cooldown_seconds": PIN_COOLDOWN_SECONDS,
        }

    def diagnostics(self) -> dict[str, Any]:
        """Return privacy-safe counts only."""
        return {
            "configured_rules": len(self._rules),
            "pin_configured": self._pin_hash is not None,
            "pending_challenges": len(self._pending),
        }

    def cancel_pending(self) -> None:
        """Fail closed when an entity is reloaded or removed."""
        self._pending.clear()
        self._cooldowns.clear()
        self._pin_failures.clear()

    async def async_backup_data(self) -> dict[str, Any]:
        """Back up the one-way PIN representation, never plaintext."""
        return {
            "storage_version": STORAGE_VERSION,
            "pin_hash": self._pin_hash,
            "rules": [dict(rule) for rule in self._rules],
        }

    @staticmethod
    def validate_backup_data(value: Any) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise ValueError("protected_actions must be an object")
        if set(value) - {"storage_version", "pin_hash", "rules"}:
            raise ValueError("unknown protected_actions fields")
        pin_hash = value.get("pin_hash")
        if pin_hash is not None and (
            not isinstance(pin_hash, str) or not _valid_pin_hash(pin_hash)
        ):
            raise ValueError("invalid protected PIN representation")
        raw_rules = value.get("rules", [])
        if not isinstance(raw_rules, Sequence) or isinstance(raw_rules, str):
            raise ValueError("protected_actions.rules must be a list")
        if len(raw_rules) > MAX_RULES:
            raise ValueError("Protected Action rule limit reached")
        rules = [validate_protection_rule(item) for item in raw_rules]
        if any(rule["protection"] == PROTECTION_PIN for rule in rules) and not pin_hash:
            raise ValueError("PIN-protected rules require a configured PIN")
        return {"pin_hash": pin_hash, "rules": rules}

    async def async_replace_backup(self, value: Any) -> None:
        prepared = self.validate_backup_data(value)
        async with self._lock:
            self._pin_hash = prepared["pin_hash"]
            self._rules = prepared["rules"]
            self._pending.clear()
            self._cooldowns.clear()
            self._pin_failures.clear()
            self._sort()
            self._initialized = True
            await self._async_save_locked()

    async def async_set_pin(self, pin: str) -> None:
        normalized = normalize_spoken_pin(pin)
        if normalized is None or not 4 <= len(normalized) <= 12:
            raise ValueError("PIN must contain 4 to 12 digits")
        pin_hash = await _async_hash_pin(normalized)
        async with self._lock:
            self._pin_hash = pin_hash
            self._pending.clear()
            self._cooldowns.clear()
            self._pin_failures.clear()
            await self._async_save_locked()

    async def async_remove_pin(self) -> None:
        async with self._lock:
            if any(rule["protection"] == PROTECTION_PIN for rule in self._rules):
                raise ValueError("Remove PIN-protected rules before removing the PIN")
            self._pin_hash = None
            self._pending.clear()
            self._cooldowns.clear()
            self._pin_failures.clear()
            await self._async_save_locked()

    async def async_create(self, value: Any) -> dict[str, Any]:
        raw = dict(value) if isinstance(value, Mapping) else {}
        raw.setdefault("id", uuid4().hex)
        raw.setdefault("order", len(self._rules))
        rule = validate_protection_rule(raw)
        async with self._lock:
            self._ensure_pin(rule)
            if len(self._rules) >= MAX_RULES:
                raise ValueError("Protected Action rule limit reached")
            self._rules.append(rule)
            self._sort()
            await self._async_save_locked()
        return dict(rule)

    async def async_update(self, rule_id: str, value: Any) -> dict[str, Any]:
        async with self._lock:
            index = self._index(rule_id)
            raw = dict(value) if isinstance(value, Mapping) else {}
            raw["id"] = rule_id
            raw.setdefault("order", self._rules[index]["order"])
            rule = validate_protection_rule(raw)
            self._ensure_pin(rule)
            self._rules[index] = rule
            self._pending.clear()
            self._cooldowns.clear()
            self._pin_failures.clear()
            self._sort()
            await self._async_save_locked()
        return dict(rule)

    async def async_delete(self, rule_id: str) -> bool:
        async with self._lock:
            del self._rules[self._index(rule_id)]
            self._pending.clear()
            self._cooldowns.clear()
            self._pin_failures.clear()
            await self._async_save_locked()
        return True

    async def async_require(
        self, context: ProtectionContext, actions: Sequence[Mapping[str, Any]]
    ) -> None:
        """Create one challenge for the strongest matching rule in a sequence."""
        async with self._lock:
            self._require_locked(context, actions)

    def _require_locked(
        self, context: ProtectionContext, actions: Sequence[Mapping[str, Any]]
    ) -> None:
        """Create a challenge while the manager lock is held."""
        prepared = [_copy_action(action) for action in actions]
        resolved_actions = [
            _resolve_target_entity_ids(self._hass, action) for action in prepared
        ]
        resolved_rules = {
            rule["id"]: {
                key: _resolve_target_entity_ids(self._hass, {key: rule.get(key, [])})
                for key in ("entity_id", "device_id", "area_id")
            }
            for rule in self._rules
            if rule["enabled"]
        }
        matches = [
            (rule, action)
            for action, resolved in zip(prepared, resolved_actions, strict=True)
            for rule in self._rules
            if rule["enabled"]
            and action_matches_rule(
                action,
                rule,
                resolved_entity_ids=resolved,
                resolved_rule_entities=resolved_rules[rule["id"]],
            )
        ]
        if not matches:
            return
        rule, _matched_action = max(
            matches,
            key=lambda item: (
                2 if item[0]["protection"] == PROTECTION_PIN else 1,
                _rule_specificity(item[0]),
                -item[0]["order"],
            ),
        )
        protection = rule["protection"]
        key = context.key
        source_key = context.source_key
        now = monotonic()
        self._prune(now)
        if protection == PROTECTION_PIN:
            if self._pin_hash is None:
                _LOGGER.warning("Protected action blocked because no PIN is configured")
                raise ProtectedActionRequired(
                    "This action requires a PIN, but no PIN is configured."
                )
            if self._cooldowns.get(source_key, 0) > now:
                raise ProtectedActionRequired(
                    "Too many PIN attempts. Please wait before trying again."
                )
        self._pending[key] = PendingChallenge(
            context=context,
            actions=prepared,
            protection=protection,
            rule_id=rule["id"],
            rule_name=rule["name"],
            expires_at=now + CHALLENGE_TTL_SECONDS,
        )
        _LOGGER.info("Protected action matched: %s", rule["name"])
        _LOGGER.info("Protection required: %s", protection)
        if protection == PROTECTION_PIN:
            raise ProtectedActionRequired("Please say your PIN.")
        raise ProtectedActionRequired("Are you sure you want to perform this action?")

    async def async_handle_reply(
        self, context: ProtectionContext, text: str
    ) -> ChallengeReply:
        """Handle an exact confirmation or PIN locally without provider access."""
        async with self._lock:
            return await self._async_handle_reply_locked(context, text)

    async def _async_handle_reply_locked(
        self, context: ProtectionContext, text: str
    ) -> ChallengeReply:
        """Handle a reply while serializing challenge consumption."""
        now = monotonic()
        pending = self._pending.get(context.key)
        if pending is not None and pending.expires_at <= now:
            self._pending.pop(context.key, None)
            self._prune(now)
            return ChallengeReply(
                True,
                "That request expired. Please ask again.",
                redact_input=pending.protection == PROTECTION_PIN,
            )
        self._prune(now)
        pending = self._pending.get(context.key)
        if pending is None:
            return ChallengeReply(False)
        normalized_phrase = _normalize_phrase(text)
        if normalized_phrase in CANCEL_PHRASES:
            self._pending.pop(context.key, None)
            _LOGGER.info("Protected action challenge cancelled")
            return ChallengeReply(
                True,
                "Cancelled.",
                redact_input=pending.protection == PROTECTION_PIN,
            )
        if pending.protection == PROTECTION_CONFIRMATION:
            if normalized_phrase not in CONFIRM_PHRASES:
                return ChallengeReply(
                    True, "Please say yes to confirm, or no to cancel."
                )
            self._pending.pop(context.key, None)
            _LOGGER.info("Protected action confirmation accepted")
            return ChallengeReply(True, "Done.", tuple(pending.actions))

        entered = normalize_spoken_pin(text)
        if entered is None:
            return ChallengeReply(
                True, "Please say your PIN one digit at a time.", redact_input=True
            )
        accepted = await _async_verify_pin(entered, self._pin_hash)
        if accepted:
            self._pending.pop(context.key, None)
            self._cooldowns.pop(context.source_key, None)
            self._pin_failures.pop(context.source_key, None)
            _LOGGER.info("PIN challenge result: accepted")
            return ChallengeReply(True, "Done.", tuple(pending.actions), True)
        pending.failed_attempts += 1
        source_key = context.source_key
        previous_attempts, last_failure = self._pin_failures.get(source_key, (0, now))
        if now - last_failure >= PIN_COOLDOWN_SECONDS:
            previous_attempts = 0
        source_attempts = previous_attempts + 1
        self._pin_failures[source_key] = (source_attempts, now)
        self._bound_pin_sources()
        _LOGGER.info("PIN challenge result: failed")
        if source_attempts >= MAX_PIN_ATTEMPTS:
            self._pending.pop(context.key, None)
            self._cooldowns[source_key] = now + PIN_COOLDOWN_SECONDS
            return ChallengeReply(
                True,
                "That PIN was not accepted. Please wait before trying again.",
                redact_input=True,
            )
        return ChallengeReply(True, "That PIN was not accepted.", redact_input=True)

    def _prune(self, now: float) -> None:
        for key, pending in list(self._pending.items()):
            if pending.expires_at <= now:
                self._pending.pop(key, None)
        for key, expires_at in list(self._cooldowns.items()):
            if expires_at <= now:
                self._cooldowns.pop(key, None)
                self._pin_failures.pop(key, None)
        for key, (_attempts, last_failure) in list(self._pin_failures.items()):
            if (
                key not in self._cooldowns
                and last_failure + PIN_COOLDOWN_SECONDS <= now
            ):
                self._pin_failures.pop(key, None)

    def _bound_pin_sources(self) -> None:
        """Keep source-scoped ephemeral authentication state bounded."""
        overflow = len(self._pin_failures) - MAX_PIN_SOURCES
        if overflow <= 0:
            return
        oldest = sorted(self._pin_failures, key=lambda key: self._pin_failures[key][1])
        for key in oldest[:overflow]:
            if self._cooldowns.get(key, 0) <= monotonic():
                self._pin_failures.pop(key, None)
        overflow = len(self._pin_failures) - MAX_PIN_SOURCES
        if overflow > 0:
            for key in oldest[:overflow]:
                self._pin_failures.pop(key, None)
                self._cooldowns.pop(key, None)

    def _ensure_pin(self, rule: Mapping[str, Any]) -> None:
        if rule["protection"] == PROTECTION_PIN and self._pin_hash is None:
            raise ValueError("Set a PIN before creating a PIN-protected rule")

    def _index(self, rule_id: str) -> int:
        for index, rule in enumerate(self._rules):
            if rule["id"] == rule_id:
                return index
        raise ValueError("Protected Action rule not found")

    def _sort(self) -> None:
        self._rules.sort(key=lambda item: (item["order"], item["name"].casefold()))

    async def _async_save_locked(self) -> None:
        await self._store.async_save({"pin_hash": self._pin_hash, "rules": self._rules})


def normalize_spoken_pin(value: str) -> str | None:
    """Normalize only unambiguous numeric or individually spoken digits."""
    if not isinstance(value, str):
        return None
    text = value.strip().casefold()
    if not text:
        return None
    if re.fullmatch(r"\d+", text):
        return text
    if re.search(r"[^a-z0-9\s,.'!?]", text):
        return None
    tokens = re.findall(r"[a-z]+|\d+", text)
    if not tokens:
        return None
    digits: list[str] = []
    for token in tokens:
        if token.isdigit():
            if len(token) != 1:
                return None
            digits.append(token)
        elif token in _DIGIT_WORDS:
            digits.append(_DIGIT_WORDS[token])
        else:
            return None
    return "".join(digits) or None


def validate_protection_rule(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("rule must be an object")
    allowed = {
        "id",
        "name",
        "enabled",
        "domain",
        "service",
        "protection",
        "entity_id",
        "device_id",
        "area_id",
        "order",
    }
    if set(value) - allowed:
        raise ValueError("unknown Protected Action rule fields")
    rule = {
        "id": _clean(value.get("id"), "rule id", 64),
        "name": _clean(value.get("name"), "rule name", 120),
        "enabled": value.get("enabled", True),
        "domain": _clean(value.get("domain"), "domain", 64).lower(),
        "service": _clean(value.get("service"), "action", 64).lower(),
        "protection": value.get("protection"),
        "entity_id": _string_list(value.get("entity_id"), "entity_id"),
        "device_id": _string_list(value.get("device_id"), "device_id"),
        "area_id": _string_list(value.get("area_id"), "area_id"),
        "order": value.get("order", 0),
    }
    if not isinstance(rule["enabled"], bool):
        raise ValueError("enabled must be true or false")
    if rule["protection"] not in PROTECTION_LEVELS:
        raise ValueError("protection must be confirmation or pin")
    if not re.fullmatch(r"[a-z0-9_]+", rule["domain"]) or not re.fullmatch(
        r"[a-z0-9_]+", rule["service"]
    ):
        raise ValueError("domain and action must use Home Assistant identifiers")
    if isinstance(rule["order"], bool) or not isinstance(rule["order"], int):
        raise ValueError("order must be an integer")
    return rule


def action_matches_rule(
    action: Mapping[str, Any],
    rule: Mapping[str, Any],
    *,
    resolved_entity_ids: set[str] | None = None,
    resolved_rule_entities: Mapping[str, set[str]] | None = None,
) -> bool:
    if str(action.get("domain", "")).casefold() != rule["domain"]:
        return False
    if str(action.get("service", "")).casefold() != rule["service"]:
        return False
    raw_data = action.get("data")
    raw_target = action.get("target")
    data = dict(raw_data) if isinstance(raw_data, Mapping) else {}
    selected_target = dict(raw_target) if isinstance(raw_target, Mapping) else {}
    target = {**data, **selected_target}
    for key in ("entity_id", "device_id", "area_id"):
        expected = set(rule.get(key, []))
        if not expected:
            continue
        literal_match = bool(expected.intersection(_values(target.get(key))))
        entity_match = bool(
            resolved_entity_ids
            and resolved_rule_entities
            and resolved_entity_ids.intersection(resolved_rule_entities.get(key, set()))
        )
        if not literal_match and not entity_match:
            return False
    return True


def _resolve_target_entity_ids(
    hass: HomeAssistant | None, value: Mapping[str, Any]
) -> set[str]:
    """Resolve supported HA selectors to affected entities, retaining literals."""
    raw_data = value.get("data")
    raw_target = value.get("target")
    data = dict(raw_data) if isinstance(raw_data, Mapping) else {}
    target = dict(raw_target) if isinstance(raw_target, Mapping) else {}
    if not data and not target:
        target = dict(value)
    selection_data = {
        key: ({**data, **target}).get(key)
        for key in ("entity_id", "device_id", "area_id", "floor_id", "label_id")
        if ({**data, **target}).get(key) not in (None, "", [])
    }
    entities = _values(selection_data.get("entity_id"))
    if hass is None or not selection_data:
        return entities
    try:
        referenced = target_helpers.async_extract_referenced_entity_ids(
            hass, target_helpers.TargetSelection(selection_data)
        )
    except HomeAssistantError, KeyError, TypeError, ValueError:
        _LOGGER.debug("Unable to resolve Protected Action target selectors")
        return entities
    return entities | set(referenced.referenced) | set(referenced.indirectly_referenced)


def _copy_action(action: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "domain": str(action.get("domain", "")),
        "service": str(action.get("service", "")),
        "data": dict(action.get("data", {})),
        "target": dict(action.get("target", {})),
    }


def _values(value: Any) -> set[str]:
    if isinstance(value, str):
        return {item.strip() for item in value.split(",") if item.strip()}
    if isinstance(value, Sequence):
        return {str(item) for item in value if isinstance(item, str) and item}
    return set()


def _string_list(value: Any, field: str) -> list[str]:
    if value in (None, "", []):
        return []
    values = [value] if isinstance(value, str) else value
    if not isinstance(values, Sequence) or any(
        not isinstance(item, str) or not item.strip() for item in values
    ):
        raise ValueError(f"{field} must contain identifiers")
    return list(dict.fromkeys(item.strip() for item in values))


def _clean(value: Any, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required")
    result = value.strip()
    if len(result) > maximum:
        raise ValueError(f"{field} is too long")
    return result


def _rule_specificity(rule: Mapping[str, Any]) -> int:
    return sum(bool(rule.get(key)) for key in ("entity_id", "device_id", "area_id"))


def _normalize_phrase(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z\s]+", " ", value.casefold())).strip()


async def _async_hash_pin(pin: str) -> str:
    salt = secrets.token_bytes(16)
    digest = await asyncio.to_thread(
        hashlib.pbkdf2_hmac, "sha256", pin.encode(), salt, PBKDF2_ITERATIONS
    )
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


async def _async_verify_pin(pin: str, encoded: str | None) -> bool:
    if encoded is None or not _valid_pin_hash(encoded):
        return False
    _, iterations, salt, expected = encoded.split("$", 3)
    digest = await asyncio.to_thread(
        hashlib.pbkdf2_hmac,
        "sha256",
        pin.encode(),
        bytes.fromhex(salt),
        int(iterations),
    )
    return hmac.compare_digest(digest.hex(), expected)


def _valid_pin_hash(value: str) -> bool:
    try:
        algorithm, iterations, salt, digest = value.split("$", 3)
        return (
            algorithm == "pbkdf2_sha256"
            and int(iterations) >= PBKDF2_ITERATIONS
            and len(bytes.fromhex(salt)) >= 16
            and len(bytes.fromhex(digest)) == 32
        )
    except TypeError, ValueError:
        return False


async def async_get_protected_actions(
    hass: HomeAssistant, entry_id: str, subentry_id: str
) -> ProtectedActions:
    key = f"{DOMAIN}.protected_actions_managers"
    managers: dict[tuple[str, str], ProtectedActions] = hass.data.setdefault(key, {})
    manager_key = (entry_id, subentry_id)
    manager = managers.get(manager_key)
    if manager is None:
        manager = ProtectedActions(
            ProtectedActionStore(
                hass,
                STORAGE_VERSION,
                f"{STORAGE_KEY_PREFIX}.{entry_id}.{subentry_id}",
                private=True,
            ),
            hass,
        )
        managers[manager_key] = manager
    await manager.async_initialize()
    return manager
