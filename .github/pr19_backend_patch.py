from pathlib import Path


def replace(path: str, old: str, new: str, count: int = 1) -> None:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    actual = text.count(old)
    if actual != count:
        raise SystemExit(
            f"{path}: expected {count} matches, found {actual}\n--- OLD ---\n{old[:1000]}"
        )
    file_path.write_text(text.replace(old, new), encoding="utf-8")


path = "custom_components/extended_openai_conversation_responses/agent_config.py"
replace(
    path,
    '''class AgentConfigError(HomeAssistantError):
    """A validation error tied to one agent configuration field."""

    def __init__(self, field: str, message: str) -> None:
        self.field = field
        super().__init__(f"{field}: {message}")


def _tools_yaml(value: Any) -> str:
''',
    '''class AgentConfigError(HomeAssistantError):
    """A validation error tied to one agent configuration field."""

    def __init__(self, field: str, message: str) -> None:
        self.field = field
        super().__init__(f"{field}: {message}")


MAX_AGENT_TITLE_LENGTH = 255


def validate_agent_title(value: Any, *, default: str | None = None) -> str:
    """Return one canonical conversation-agent title for every persistence path."""
    if value is None and default is not None:
        value = default
    if not isinstance(value, str):
        raise AgentConfigError("title", "must be a string")
    title = value.strip()
    if not title:
        raise AgentConfigError("title", "must not be empty")
    if len(title) > MAX_AGENT_TITLE_LENGTH:
        raise AgentConfigError(
            "title", f"must be at most {MAX_AGENT_TITLE_LENGTH} characters"
        )
    return title


def _tools_yaml(value: Any) -> str:
''',
)

path = "custom_components/extended_openai_conversation_responses/request_rules.py"
replace(path, "import asyncio\n", "import asyncio\nfrom hashlib import sha256\nimport json\n")
replace(
    path,
    '''    def snapshot(self) -> dict[str, Any]:
        """Return a copy suitable for the management API."""
        return {
            "storage_version": STORAGE_VERSION,
            "defaults": dict(self._defaults),
            "wording_groups": _copy_wording_groups(self._wording_groups),
            "rules": [dict(rule) for rule in self._rules],
        }
''',
    '''    def revision(self) -> str:
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
        }
''',
)
replace(
    path,
    '''    async def async_backup_data(self) -> dict[str, Any]:
        """Return durable Request Rule state for the per-agent backup."""
        return self.snapshot()
''',
    '''    async def async_backup_data(self) -> dict[str, Any]:
        """Return durable Request Rule state without the management revision token."""
        snapshot = self.snapshot()
        snapshot.pop("revision", None)
        return snapshot
''',
)
replace(
    path,
    '''    async def async_set_defaults(self, value: Any) -> dict[str, Any]:
        """Replace global matching defaults."""
        defaults = validate_matching_settings(value)
        async with self._lock:
            self._defaults = defaults
''',
    '''    async def async_set_defaults(
        self, value: Any, *, expected_revision: str | None = None
    ) -> dict[str, Any]:
        """Replace global matching defaults."""
        defaults = validate_matching_settings(value)
        async with self._lock:
            self._require_revision_locked(expected_revision)
            self._defaults = defaults
''',
)
replace(
    path,
    '''    async def async_set_wording_groups(self, value: Any) -> list[dict[str, Any]]:
        """Replace the persisted wording synonym groups."""
        groups = validate_wording_groups(value)
        async with self._lock:
            self._wording_groups = groups
''',
    '''    async def async_set_wording_groups(
        self, value: Any, *, expected_revision: str | None = None
    ) -> list[dict[str, Any]]:
        """Replace the persisted wording synonym groups."""
        groups = validate_wording_groups(value)
        async with self._lock:
            self._require_revision_locked(expected_revision)
            self._wording_groups = groups
''',
)
replace(
    path,
    '''    async def async_create(self, value: Any) -> dict[str, Any]:
        """Create one rule."""
        if not isinstance(value, Mapping):
            raise ValueError("rule must be an object")
        async with self._lock:
            if len(self._rules) >= MAX_RULES:
''',
    '''    async def async_create(
        self, value: Any, *, expected_revision: str | None = None
    ) -> dict[str, Any]:
        """Create one rule."""
        if not isinstance(value, Mapping):
            raise ValueError("rule must be an object")
        async with self._lock:
            self._require_revision_locked(expected_revision)
            if len(self._rules) >= MAX_RULES:
''',
)
replace(
    path,
    '''    async def async_update(self, rule_id: str, value: Any) -> dict[str, Any]:
        """Replace one rule while preserving its id."""
        if not isinstance(value, Mapping):
            raise ValueError("rule must be an object")
        async with self._lock:
            index = self._index(rule_id)
''',
    '''    async def async_update(
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
''',
)
replace(
    path,
    '''    async def async_delete(self, rule_id: str) -> bool:
        """Delete one rule."""
        async with self._lock:
            index = self._index(rule_id)
''',
    '''    async def async_delete(
        self, rule_id: str, *, expected_revision: str | None = None
    ) -> bool:
        """Delete one rule."""
        async with self._lock:
            self._require_revision_locked(expected_revision)
            index = self._index(rule_id)
''',
)
replace(
    path,
    '''    async def async_duplicate(self, rule_id: str) -> dict[str, Any]:
        """Duplicate one rule immediately after its source."""
        async with self._lock:
            if len(self._rules) >= MAX_RULES:
''',
    '''    async def async_duplicate(
        self, rule_id: str, *, expected_revision: str | None = None
    ) -> dict[str, Any]:
        """Duplicate one rule immediately after its source."""
        async with self._lock:
            self._require_revision_locked(expected_revision)
            if len(self._rules) >= MAX_RULES:
''',
)

path = "custom_components/extended_openai_conversation_responses/backup.py"
replace(
    path,
    '''from .agent_config import (
    agent_config_snapshot,
    normalize_agent_config,
    preserve_legacy_guest_policy,
)
''',
    '''from .agent_config import (
    agent_config_snapshot,
    normalize_agent_config,
    preserve_legacy_guest_policy,
    validate_agent_title,
)
''',
)
replace(
    path,
    "from .request_rules import RequestRules, async_get_request_rules\n",
    "from .request_rules import RequestRules, async_get_request_rules\nfrom .secret_redaction import redact_secrets, restore_redacted_secrets\n",
)
replace(
    path,
    '''_SECRET_KEY_PARTS = frozenset(
    {"password", "passwd", "secret", "token", "authorization"}
)
_SECRET_KEY_FAMILIES = ("apikey", "clientsecret", "accesstoken", "refreshtoken")
_CAMEL_CASE_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_KEY_SEPARATOR = re.compile(r"[^A-Za-z0-9]+")
_LIKELY_SECRET = re.compile(r"\\bsk-[A-Za-z0-9_-]{12,}\\b")
''',
    "",
)
replace(
    path,
    '''def _is_secret_key(key: Any) -> bool:
    """Classify credential-like keys without depending on separator spelling."""
    separated = _CAMEL_CASE_BOUNDARY.sub(" ", str(key))
    parts = tuple(part.casefold() for part in _KEY_SEPARATOR.split(separated) if part)
    if any(part in _SECRET_KEY_PARTS for part in parts):
        return True
    canonical = "".join(parts)
    return any(family in canonical for family in _SECRET_KEY_FAMILIES)


def _safe_configuration(value: Any, *, schema: bool = False) -> Any:
    """Remove common credential fields and unmistakable key literals."""
    if isinstance(value, list):
        return [_safe_configuration(item, schema=schema) for item in value]
    if isinstance(value, str):
        return _LIKELY_SECRET.sub("[redacted]", value)
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        child_schema = schema or key in {"parameters", "properties", "items"}
        if not schema and _is_secret_key(key):
            continue
        result[key] = _safe_configuration(item, schema=child_schema)
    return result
''',
    '''def _safe_configuration(value: Any, *, schema: bool = False) -> Any:
    """Redact credential values while preserving the exported object shape."""
    return redact_secrets(value, schema=schema)
''',
)
replace(
    path,
    '''    title = agent["title"]
    if not isinstance(title, str) or not title.strip() or len(title.strip()) > 255:
        raise BackupError("The backed-up agent name is invalid")
''',
    '''    try:
        title = validate_agent_title(agent["title"])
    except HomeAssistantError as err:
        raise BackupError("The backed-up agent name is invalid") from err
''',
)
replace(
    path,
    '''        raw_config = agent["config"]
        if not isinstance(raw_config, dict):
            raise ValueError("agent config must be an object")
        config = preserve_legacy_guest_policy(
            raw_config, normalize_agent_config(raw_config)
        )
''',
    '''        raw_config = restore_redacted_secrets(agent["config"])
        if not isinstance(raw_config, dict):
            raise ValueError("agent config must be an object")
        config = preserve_legacy_guest_policy(
            raw_config, normalize_agent_config(raw_config)
        )
''',
)
replace(
    path,
    '''        request_rules = RequestRules.validate_backup_data(
            value.get("request_rules", {"defaults": {}, "rules": []})
        )
''',
    '''        request_rules = RequestRules.validate_backup_data(
            restore_redacted_secrets(
                value.get("request_rules", {"defaults": {}, "rules": []})
            )
        )
''',
)
replace(path, "        title.strip(),\n        config,\n", "        title,\n        config,\n")

path = "custom_components/extended_openai_conversation_responses/management_ui.py"
replace(
    path,
    "from collections.abc import Mapping\nimport json\n",
    "from collections.abc import Mapping\nfrom hashlib import sha256\nimport json\n",
)
replace(
    path,
    '''    normalize_agent_config,
    preserve_legacy_guest_policy,
    starter_function_tool_yaml,
''',
    '''    normalize_agent_config,
    preserve_legacy_guest_policy,
    MAX_AGENT_TITLE_LENGTH,
    starter_function_tool_yaml,
    validate_agent_title,
''',
)
replace(
    path,
    "from .scope import SHARED_HOUSEHOLD_SCOPE_ID, user_scope\n",
    "from .scope import SHARED_HOUSEHOLD_SCOPE_ID, user_scope\nfrom .secret_redaction import redact_secrets, restore_redacted_secrets\n",
)
replace(
    path,
    '''def _validation_result(callback) -> dict[str, Any]:
    """Run configuration validation and return frontend-friendly errors."""
    try:
        value = callback()
    except AgentConfigError as err:
        return {"valid": False, "errors": {err.field: str(err).split(": ", 1)[-1]}}
    return {"valid": True, "errors": {}, "config": value}


def _persist_function_configuration(
''',
    '''def _validation_result(callback) -> dict[str, Any]:
    """Run configuration validation and return frontend-friendly errors."""
    try:
        value = callback()
    except AgentConfigError as err:
        return {"valid": False, "errors": {err.field: str(err).split(": ", 1)[-1]}}
    return {"valid": True, "errors": {}, "config": value}


def _agent_config_revision(data: Mapping[str, Any], title: str) -> str:
    """Return a stable optimistic-concurrency token for one saved agent config."""
    payload = canonical_json(
        {"title": title, "config": agent_config_snapshot(dict(data))}
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def _require_agent_config_revision(subentry: Any, expected_revision: Any) -> None:
    """Reject a stale management writer before it can replace newer settings."""
    if expected_revision is None:
        return
    if not isinstance(expected_revision, str):
        raise HomeAssistantError("revision must be a string")
    if expected_revision != _agent_config_revision(subentry.data, subentry.title):
        raise HomeAssistantError(
            "Configuration changed in another tab. Reload the latest saved settings before saving."
        )


def _persist_function_configuration(
''',
)
replace(
    path,
    '''_SECRET_KEY = re.compile(
    r"(?:^|[_-])(?:api_?key|password|passwd|secret|token|authorization)(?:$|[_-])"
    r"|(?:apiKey|clientSecret|accessToken|refreshToken)$",
    re.IGNORECASE,
)


def _redact_export_secrets(value: Any, *, schema: bool = False) -> Any:
    """Remove likely credential values while preserving JSON-schema properties."""
    if isinstance(value, list):
        return [_redact_export_secrets(item, schema=schema) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        child_schema = schema or key in {"parameters", "properties", "items"}
        if not schema and _SECRET_KEY.search(str(key)):
            continue
        result[key] = _redact_export_secrets(item, schema=child_schema)
    return result
''',
    '''def _redact_export_secrets(value: Any, *, schema: bool = False) -> Any:
    """Redact credential values while preserving exported configuration structure."""
    return redact_secrets(value, schema=schema)
''',
)
replace(
    path,
    '''    config = value.get("config")
    if not isinstance(config, dict):
        raise AgentConfigError("config", "must be an object")
    return {
        "title": str(value.get("title") or "Imported conversation agent").strip(),
        "config": preserve_legacy_guest_policy(config, normalize_agent_config(config)),
    }
''',
    '''    config = restore_redacted_secrets(value.get("config"))
    if not isinstance(config, dict):
        raise AgentConfigError("config", "must be an object")
    return {
        "title": validate_agent_title(
            value.get("title"), default="Imported conversation agent"
        ),
        "config": preserve_legacy_guest_policy(config, normalize_agent_config(config)),
    }
''',
)
replace(
    path,
    '''            return {
                "title": subentry.title,
                "config": config,
''',
    '''            return {
                "title": subentry.title,
                "revision": _agent_config_revision(subentry.data, subentry.title),
                "config": config,
''',
)
replace(
    path,
    '''        if action == "update":
            _require_admin(is_admin)
            updates = message.get("config")
            if not isinstance(updates, dict):
                raise HomeAssistantError("config must be an object")
            normalized = merge_agent_config(subentry.data, updates)
            if CONF_GUEST_POLICY_VERSION not in subentry.data:
                # The general configuration editor must not implicitly accept
                # the v2 Guest migration draft. Only Guest Mode's explicit save
                # action crosses this boundary.
                for key in GUEST_V2_FIELDS:
                    normalized.pop(key, None)
            title = message.get("title")
            if title is not None and (not isinstance(title, str) or not title.strip()):
                raise AgentConfigError("title", "must not be empty")
            hass.config_entries.async_update_subentry(
                entry,
                subentry,
                data=normalized,
                **({"title": title.strip()} if isinstance(title, str) else {}),
            )
            snapshot = agent_config_snapshot(normalized)
            return {
                "title": title.strip() if isinstance(title, str) else subentry.title,
                "config": snapshot,
''',
    '''        if action == "update":
            _require_admin(is_admin)
            updates = message.get("config")
            if not isinstance(updates, dict):
                raise HomeAssistantError("config must be an object")
            _require_agent_config_revision(subentry, message.get("revision"))
            normalized = merge_agent_config(subentry.data, updates)
            if CONF_GUEST_POLICY_VERSION not in subentry.data:
                # The general configuration editor must not implicitly accept
                # the v2 Guest migration draft. Only Guest Mode's explicit save
                # action crosses this boundary.
                for key in GUEST_V2_FIELDS:
                    normalized.pop(key, None)
            requested_title = message.get("title")
            saved_title = (
                validate_agent_title(requested_title)
                if requested_title is not None
                else validate_agent_title(subentry.title)
            )
            hass.config_entries.async_update_subentry(
                entry, subentry, data=normalized, title=saved_title
            )
            snapshot = agent_config_snapshot(normalized)
            return {
                "title": saved_title,
                "revision": _agent_config_revision(normalized, saved_title),
                "config": snapshot,
''',
)
replace(
    path,
    '''            requested_title = message.get("title")
            title = (
                requested_title.strip()
                if isinstance(requested_title, str) and requested_title.strip()
                else f"{subentry.title} - Copy"
            )
''',
    '''            requested_title = message.get("title")
            if requested_title is None:
                suffix = " - Copy"
                source = validate_agent_title(subentry.title)
                base = source[: MAX_AGENT_TITLE_LENGTH - len(suffix)].rstrip()
                title = validate_agent_title(f"{base}{suffix}")
            else:
                title = validate_agent_title(requested_title)
''',
)
replace(
    path,
    '''            if mode == "current":
                if message.get("confirm") is not True:
                    raise HomeAssistantError("Explicit confirmation is required")
                hass.config_entries.async_update_subentry(
                    entry, subentry, data=parsed["config"], title=parsed["title"]
                )
                return {"status": "updated", "subentry_id": subentry.subentry_id}
''',
    '''            if mode == "current":
                if message.get("confirm") is not True:
                    raise HomeAssistantError("Explicit confirmation is required")
                _require_agent_config_revision(subentry, message.get("revision"))
                hass.config_entries.async_update_subentry(
                    entry, subentry, data=parsed["config"], title=parsed["title"]
                )
                return {
                    "status": "updated",
                    "subentry_id": subentry.subentry_id,
                    "revision": _agent_config_revision(
                        parsed["config"], parsed["title"]
                    ),
                }
''',
)
replace(
    path,
    '''        if action == "defaults":
            return {"defaults": await rules.async_set_defaults(message.get("defaults"))}
        if action == "wording_groups":
            return {
                "wording_groups": await rules.async_set_wording_groups(
                    message.get("wording_groups")
                )
            }
''',
    '''        if action == "defaults":
            defaults = await rules.async_set_defaults(
                message.get("defaults"), expected_revision=message.get("revision")
            )
            return {"defaults": defaults, "revision": rules.revision()}
        if action == "wording_groups":
            wording_groups = await rules.async_set_wording_groups(
                message.get("wording_groups"),
                expected_revision=message.get("revision"),
            )
            return {"wording_groups": wording_groups, "revision": rules.revision()}
''',
)
replace(
    path,
    '            return {"rule": await rules.async_create(candidate)}\n',
    '''            rule = await rules.async_create(
                candidate, expected_revision=message.get("revision")
            )
            return {"rule": rule, "revision": rules.revision()}
''',
)
replace(
    path,
    '            return {"rule": await rules.async_update(rule_id, candidate)}\n',
    '''            rule = await rules.async_update(
                rule_id, candidate, expected_revision=message.get("revision")
            )
            return {"rule": rule, "revision": rules.revision()}
''',
)
replace(
    path,
    '''            return {"deleted": await rules.async_delete(rule_id)}
        if action == "duplicate":
            return {"rule": await rules.async_duplicate(rule_id)}
''',
    '''            deleted = await rules.async_delete(
                rule_id, expected_revision=message.get("revision")
            )
            return {"deleted": deleted, "revision": rules.revision()}
        if action == "duplicate":
            rule = await rules.async_duplicate(
                rule_id, expected_revision=message.get("revision")
            )
            return {"rule": rule, "revision": rules.revision()}
''',
)

path = "tests/test_backup_credential_redaction.py"
replace(
    path,
    '''from custom_components.extended_openai_conversation_responses.backup import (
    _safe_configuration,
)
''',
    '''from custom_components.extended_openai_conversation_responses.backup import (
    _safe_configuration,
)
from custom_components.extended_openai_conversation_responses.secret_redaction import (
    REDACTED_SECRET_SENTINEL,
)
''',
)
replace(
    path,
    "    assert secret_key not in headers\n",
    "    assert headers[secret_key] == REDACTED_SECRET_SENTINEL\n",
)
replace(
    path,
    '''    assert transport["headers"] == {}
    assert transport["credentials"] == {}
''',
    '''    assert transport["headers"] == {"X-API-Key": REDACTED_SECRET_SENTINEL}
    assert transport["credentials"] == {
        "Client Secret": REDACTED_SECRET_SENTINEL,
        "refresh-token": REDACTED_SECRET_SENTINEL,
    }
''',
)

Path(".github/pr19_backend_patch.py").unlink()
Path(".github/workflows/pr19-backend-apply.yml").unlink()
