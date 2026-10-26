"""Unified portable export, custom backup, and import/restore planning."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable, Mapping
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
import json
import math
from pathlib import Path
import re
from typing import Any

import yaml

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from . import backup
from .agent_config import configured_function_tools_from_data, validate_agent_title
from .bounded_json import (
    JSONBudgetExceeded,
    bounded_json_dumps,
    check_redaction_budget,
    check_utf8_budget,
)
from .const import (
    AGENT_CONFIG_EXPORT_VERSION,
    CONF_VOICE_DEFAULT_USER_ID,
    CONF_VOICE_DEVICE_MAPPINGS,
)
from .conversation_archive import ArchiveSession, ArchiveTurn, async_get_archive
from .function_dependency_integrity import async_validate_request_rule_functions
from .function_yaml import decoded_function_fields
from .guest_mode import GuestModeManager, GuestModeSchedule, async_get_guest_mode
from .knowledge import KnowledgeSource, async_get_knowledge
from .memory import ANONYMOUS_USER_ID, MemoryRecord, PersistentMemory, async_get_memory
from .private_backup import (
    PRIVATE_BACKUP_VERSION,
    PRIVATE_TRANSFER_VERSION,
    DecodedPrivateDocument,
    decode_document,
    is_private_document,
)
from .request_rules import RequestRules, async_get_request_rules
from .secret_redaction import (
    LITERAL_TEXT_KEY,
    REDACTED_SECRET_SENTINEL,
    is_literal_text,
    is_redacted_secret,
    is_redacted_url,
    redact_http_url,
    redact_secrets,
    restore_redacted_secrets,
)
from .sharing import SHARING_VERSION, project_sharing_sections
from .temporary_memory import TemporaryMemoryRecord, async_get_temporary_memory
from .usage import UsageRequest, UsageRun, UsageTotals, async_get_usage

TRANSFER_FORMAT = "extended_openai_conversation_transfer"
TRANSFER_VERSION = 1
LEGACY_AGENT_SCHEMA = "extended_openai_conversation.agent"

SECTION_CONFIGURATION = "configuration"
SECTION_REQUEST_RULES = "request_rules"
SECTION_PERSISTENT_MEMORY = "persistent_memory"
SECTION_TEMPORARY_MEMORY = "temporary_memory"
SECTION_KNOWLEDGE = "knowledge"
SECTION_CONVERSATION_ARCHIVE = "conversation_archive"
SECTION_USAGE = "usage"
SECTION_GUEST_MODE = "guest_mode"

SETUP_SECTIONS = frozenset({SECTION_CONFIGURATION, SECTION_REQUEST_RULES})
ALL_SECTIONS = frozenset(
    {
        SECTION_CONFIGURATION,
        SECTION_REQUEST_RULES,
        SECTION_PERSISTENT_MEMORY,
        SECTION_TEMPORARY_MEMORY,
        SECTION_KNOWLEDGE,
        SECTION_CONVERSATION_ARCHIVE,
        SECTION_USAGE,
        SECTION_GUEST_MODE,
    }
)
SECTION_ORDER = (
    SECTION_CONFIGURATION,
    SECTION_REQUEST_RULES,
    SECTION_PERSISTENT_MEMORY,
    SECTION_TEMPORARY_MEMORY,
    SECTION_KNOWLEDGE,
    SECTION_CONVERSATION_ARCHIVE,
    SECTION_USAGE,
    SECTION_GUEST_MODE,
)
SECTION_LABELS = {
    SECTION_CONFIGURATION: "Agent configuration and Function Tools",
    SECTION_REQUEST_RULES: "Request Rules",
    SECTION_PERSISTENT_MEMORY: "Persistent memories",
    SECTION_TEMPORARY_MEMORY: "Active temporary memories",
    SECTION_KNOWLEDGE: "Knowledge sources",
    SECTION_CONVERSATION_ARCHIVE: "Conversation archive",
    SECTION_USAGE: "Usage history",
    SECTION_GUEST_MODE: "Guest Mode schedule",
}

_ABSENT = object()
_DROP = object()


@dataclass(slots=True)
class PreparedTransfer:
    """Validated import payload before a target-specific restore plan is applied."""

    source_kind: str
    mode: str
    title: str
    available_sections: frozenset[str]
    created_at: str | None
    integration_version: str | None
    config: dict[str, Any] | None = None
    request_rules: dict[str, Any] | None = None
    memories: list[MemoryRecord] | None = None
    temporary_memories: list[TemporaryMemoryRecord] | None = None
    knowledge: list[KnowledgeSource] | None = None
    archive_sessions: list[ArchiveSession] | None = None
    archive_turns: list[ArchiveTurn] | None = None
    usage_totals: UsageTotals | None = None
    usage_daily: dict[str, dict[str, Any]] | None = None
    usage_requests: list[UsageRequest] | None = None
    usage_runs: list[UsageRun] | None = None
    guest_mode_schedule: GuestModeSchedule | None = None
    raw_configuration: Any = None
    raw_request_rules: Any = None
    redacted_sensitive_fields: tuple[str, ...] = ()
    security: dict[str, Any] | None = None
    private: bool = False
    provider_connection: dict[str, Any] | None = None

    def summary(self) -> dict[str, Any]:
        """Return a bounded, frontend-friendly description of available content."""
        sections = [
            section for section in SECTION_ORDER if section in self.available_sections
        ]
        return {
            "source_kind": self.source_kind,
            "mode": self.mode,
            "sections": sections,
            "section_labels": {
                section: SECTION_LABELS[section] for section in sections
            },
            "configuration": SECTION_CONFIGURATION in self.available_sections,
            "request_rules": (
                len(self.request_rules.get("rules", []))
                if self.request_rules is not None
                else 0
            ),
            "persistent_memories": len(self.memories or []),
            "temporary_memories": len(self.temporary_memories or []),
            "knowledge_sources": len(self.knowledge or []),
            "archive_sessions": len(self.archive_sessions or []),
            "archive_turns": len(self.archive_turns or []),
            "usage_requests": len(self.usage_requests or []),
            "usage_runs": len(self.usage_runs or []),
            "guest_mode_scheduled": (
                SECTION_GUEST_MODE in self.available_sections
                and self.guest_mode_schedule is not None
            ),
            "created_at": self.created_at,
            "integration_version": self.integration_version,
            "redacted_sensitive_fields": list(self.redacted_sensitive_fields[:50]),
            "redacted_sensitive_field_count": len(self.redacted_sensitive_fields),
            "security": self.security,
            "private_backup": self.private,
            "provider_connection_available": bool(self.provider_connection),
        }


def _integration_version() -> str:
    manifest = json.loads((Path(__file__).parent / "manifest.json").read_text("utf-8"))
    return str(manifest["version"])


def validate_section_selection(
    sections: Iterable[str] | None,
    *,
    allowed: Iterable[str] = ALL_SECTIONS,
    default: Iterable[str] | None = None,
) -> frozenset[str]:
    """Validate a non-empty bounded section selection."""
    allowed_set = frozenset(allowed)
    if sections is None:
        selected = frozenset(default if default is not None else allowed_set)
    else:
        if isinstance(sections, (str, bytes)):
            raise backup.BackupError("Transfer sections must be a list")
        try:
            values = tuple(sections)
        except TypeError as err:
            raise backup.BackupError("Transfer sections must be a list") from err
        if not all(isinstance(item, str) for item in values):
            raise backup.BackupError("Transfer sections must contain names only")
        selected = frozenset(values)
    if not selected:
        raise backup.BackupError("Select at least one section")
    unknown = selected - allowed_set
    if unknown:
        raise backup.BackupError(
            "Unknown transfer section: " + ", ".join(sorted(unknown))
        )
    return selected


def _portable_user_id(value: Any, *, prefixed_only: bool = False) -> str | None:
    """Return one user ID carried by portable retained-data ownership."""
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    prefixed = candidate.startswith("user:")
    if prefixed:
        candidate = candidate.removeprefix("user:")
    elif prefixed_only:
        return None
    if not candidate or value in {
        ANONYMOUS_USER_ID,
        "shared:household",
        "shared",
        "unretained",
    }:
        return None
    return candidate


def transfer_user_scope_ids(
    prepared: PreparedTransfer, selected: Iterable[str]
) -> frozenset[str]:
    """Return source-installation HA users referenced by selected portable sections."""
    selected_set = frozenset(selected)
    user_ids: set[str] = set()

    if SECTION_PERSISTENT_MEMORY in selected_set:
        for record in prepared.memories or ():
            if user_id := _portable_user_id(record.user_id):
                user_ids.add(user_id)

    if SECTION_TEMPORARY_MEMORY in selected_set:
        for temporary_record in prepared.temporary_memories or ():
            if user_id := _portable_user_id(
                temporary_record.owner_scope_id, prefixed_only=True
            ):
                user_ids.add(user_id)

    if SECTION_CONVERSATION_ARCHIVE in selected_set:
        for session in prepared.archive_sessions or ():
            if user_id := _portable_user_id(session.scope_id, prefixed_only=True):
                user_ids.add(user_id)

    if SECTION_CONFIGURATION in selected_set and prepared.config is not None:
        if user_id := _portable_user_id(
            prepared.config.get(CONF_VOICE_DEFAULT_USER_ID)
        ):
            user_ids.add(user_id)
        mappings = prepared.config.get(CONF_VOICE_DEVICE_MAPPINGS, {})
        if isinstance(mappings, Mapping):
            for owner in mappings.values():
                if user_id := _portable_user_id(owner):
                    user_ids.add(user_id)

    return frozenset(user_ids)


async def async_user_scope_mapping_plan(
    hass: HomeAssistant,
    prepared: PreparedTransfer,
    selected: Iterable[str],
    supplied: Any = None,
) -> dict[str, Any]:
    """Resolve portable source-user ownership against destination HA users."""
    source_ids = transfer_user_scope_ids(prepared, selected)
    users = await hass.auth.async_get_users()
    destinations = {
        str(user.id): str(user.name or user.id)
        for user in users
        if getattr(user, "id", None) is not None
    }
    supplied = {} if supplied is None else supplied
    if not isinstance(supplied, Mapping) or any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in supplied.items()
    ):
        raise backup.BackupError("User ownership mappings must be an object")

    required = sorted(source_ids - destinations.keys())
    resolved = {user_id: user_id for user_id in source_ids if user_id in destinations}
    for source_id, destination_id in supplied.items():
        if source_id not in required:
            continue
        if destination_id not in destinations:
            raise backup.BackupError(
                f"Mapped Home Assistant user does not exist: {destination_id}"
            )
        resolved[source_id] = destination_id

    missing = [source_id for source_id in required if source_id not in resolved]
    return {
        "required_source_user_ids": required,
        "missing_source_user_ids": missing,
        "destination_users": [
            {"user_id": user_id, "name": name}
            for user_id, name in sorted(
                destinations.items(), key=lambda item: (item[1].casefold(), item[0])
            )
        ],
        "resolved": {source_id: resolved[source_id] for source_id in sorted(resolved)},
    }


def _map_owner_value(value: Any, mapping: Mapping[str, str]) -> Any:
    """Map one retained owner while preserving its stored prefix convention."""
    source_id = _portable_user_id(value)
    if source_id is None or source_id not in mapping:
        return value
    destination = mapping[source_id]
    return (
        f"user:{destination}"
        if isinstance(value, str) and value.startswith("user:")
        else destination
    )


def _map_configuration_users(value: Any, mapping: Mapping[str, str]) -> Any:
    if not isinstance(value, Mapping):
        return value
    result = deepcopy(dict(value))
    default_user = result.get(CONF_VOICE_DEFAULT_USER_ID)
    if default_user is not None:
        result[CONF_VOICE_DEFAULT_USER_ID] = _map_owner_value(default_user, mapping)
    device_mappings = result.get(CONF_VOICE_DEVICE_MAPPINGS)
    if isinstance(device_mappings, Mapping):
        result[CONF_VOICE_DEVICE_MAPPINGS] = {
            str(device_id): _map_owner_value(owner, mapping)
            for device_id, owner in device_mappings.items()
        }
    return result


def apply_user_scope_mappings(
    prepared: PreparedTransfer,
    selected: Iterable[str],
    mapping: Mapping[str, str],
    *,
    copy_categories: bool = True,
) -> PreparedTransfer:
    """Apply explicit cross-install user ownership mappings to selected sections."""
    selected_set = frozenset(selected)
    mapped = (
        deepcopy(prepared)
        if copy_categories
        else replace(
            prepared,
            config=deepcopy(prepared.config),
            request_rules=deepcopy(prepared.request_rules),
            raw_configuration=deepcopy(prepared.raw_configuration),
            raw_request_rules=deepcopy(prepared.raw_request_rules),
        )
    )

    if SECTION_CONFIGURATION in selected_set:
        mapped.config = _map_configuration_users(mapped.config, mapping)
        mapped.raw_configuration = _map_configuration_users(
            mapped.raw_configuration, mapping
        )

    return _map_transfer_data_owners(mapped, selected_set, mapping)


def _map_transfer_data_owners(
    mapped: PreparedTransfer, selected_set: frozenset[str], mapping: Mapping[str, str]
) -> PreparedTransfer:
    """Remap detached data and check owner collisions without HA objects."""
    if SECTION_PERSISTENT_MEMORY in selected_set and mapped.memories is not None:
        mapped.memories = [
            replace(record, user_id=str(_map_owner_value(record.user_id, mapping)))
            for record in mapped.memories
        ]
        try:
            PersistentMemory.validate_backup_data(
                {"memories": [asdict(record) for record in mapped.memories]}
            )
        except ValueError as err:
            raise backup.BackupError(
                f"User ownership mapping is invalid: {err}"
            ) from err

    if (
        SECTION_TEMPORARY_MEMORY in selected_set
        and mapped.temporary_memories is not None
    ):
        records: list[TemporaryMemoryRecord] = []
        for record in mapped.temporary_memories:
            owner = _map_owner_value(record.owner_scope_id, mapping)
            scope_id = record.scope_id
            if isinstance(scope_id, str) and scope_id.startswith("user:"):
                scope_id = str(_map_owner_value(scope_id, mapping))
            records.append(
                replace(
                    record,
                    owner_scope_id=str(owner) if owner is not None else None,
                    scope_id=scope_id,
                )
            )
        mapped.temporary_memories = records

    if (
        SECTION_CONVERSATION_ARCHIVE in selected_set
        and mapped.archive_sessions is not None
    ):
        mapped.archive_sessions = [
            replace(
                session,
                scope_id=str(_map_owner_value(session.scope_id, mapping)),
            )
            for session in mapped.archive_sessions
        ]

    return mapped


async def async_apply_user_scope_mappings(
    prepared: PreparedTransfer, selected: Iterable[str], mapping: Mapping[str, str]
) -> PreparedTransfer:
    """Remap/validate bulk data off-loop; copy HA-aware configuration on-loop."""
    selected_set = frozenset(selected)
    detached = await asyncio.to_thread(
        _copy_and_map_transfer_data, prepared, selected_set, dict(mapping)
    )
    # The worker already mapped data-only categories. This pass copies HA-aware
    # fields and applies only the configuration selection, without revalidation.
    return apply_user_scope_mappings(
        detached,
        selected_set & {SECTION_CONFIGURATION},
        mapping,
        copy_categories=False,
    )


def _copy_and_map_transfer_data(
    prepared: PreparedTransfer, selected: frozenset[str], mapping: Mapping[str, str]
) -> PreparedTransfer:
    return _map_transfer_data_owners(
        _copy_transfer_categories(prepared), selected, mapping
    )


def _safe_title(title: str) -> str:
    return (
        re.sub(r"[^a-z0-9]+", "-", title.casefold()).strip("-") or "conversation-agent"
    )


def _new_transfer_document(entry: Any, subentry: Any, mode: str) -> dict[str, Any]:
    document = {
        "format": TRANSFER_FORMAT,
        "version": SHARING_VERSION if mode == "setup" else PRIVATE_TRANSFER_VERSION,
        "mode": mode,
        "created_at": dt_util.utcnow().isoformat(),
        "integration_version": _integration_version(),
        "agent": {
            "title": subentry.title,
            "source_entry_id": entry.entry_id,
            "source_subentry_id": subentry.subentry_id,
        },
        "sections": {},
        **(
            {"provider_connection": deepcopy(dict(getattr(entry, "data", {})))}
            if mode == "custom"
            else {}
        ),
    }
    return DecodedPrivateDocument(document) if mode == "custom" else document


async def async_collect_transfer_snapshot(
    hass: HomeAssistant,
    entry: Any,
    subentry: Any,
    *,
    mode: str,
    sections: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Collect only the sections requested for a portable/custom transfer."""
    from .agent_maintenance import get_agent_maintenance_gate

    get_agent_maintenance_gate(
        hass, entry.entry_id, subentry.subentry_id
    ).require_available()

    if mode == "setup":
        selected = SETUP_SECTIONS
    elif mode == "custom":
        selected = validate_section_selection(sections)
    else:
        raise backup.BackupError("Transfer mode must be setup or custom")

    document = await hass.async_add_executor_job(
        _new_transfer_document, entry, subentry, mode
    )
    if SECTION_CONFIGURATION not in selected:
        document.pop("provider_connection", None)
    payload = document["sections"]
    if SECTION_CONFIGURATION in selected:
        payload[SECTION_CONFIGURATION] = (
            deepcopy(dict(subentry.data))
            if mode == "custom"
            else backup.export_configuration_snapshot(subentry.data)
        )
    if SECTION_REQUEST_RULES in selected:
        request_rules_manager = await async_get_request_rules(
            hass, entry.entry_id, subentry.subentry_id
        )
        payload[SECTION_REQUEST_RULES] = await request_rules_manager.async_backup_data()
    if SECTION_PERSISTENT_MEMORY in selected:
        memory_manager = await async_get_memory(
            hass, entry.entry_id, subentry.subentry_id
        )
        payload[SECTION_PERSISTENT_MEMORY] = await memory_manager.async_backup_data()
    if SECTION_TEMPORARY_MEMORY in selected:
        temporary_memory_manager = await async_get_temporary_memory(
            hass, entry.entry_id, subentry.subentry_id
        )
        payload[
            SECTION_TEMPORARY_MEMORY
        ] = await temporary_memory_manager.async_backup_data()
    if SECTION_KNOWLEDGE in selected:
        knowledge_manager = await async_get_knowledge(
            hass, entry.entry_id, subentry.subentry_id
        )
        payload[SECTION_KNOWLEDGE] = await knowledge_manager.async_backup_data()
    if SECTION_CONVERSATION_ARCHIVE in selected:
        archive_manager = await async_get_archive(
            hass, entry.entry_id, subentry.subentry_id
        )
        payload[
            SECTION_CONVERSATION_ARCHIVE
        ] = await archive_manager.async_backup_data()
    if SECTION_USAGE in selected:
        usage_manager = await async_get_usage(
            hass, entry.entry_id, subentry.subentry_id
        )
        payload[SECTION_USAGE] = await usage_manager.async_backup_data()
    if SECTION_GUEST_MODE in selected:
        guest_mode_manager = await async_get_guest_mode(
            hass, entry.entry_id, subentry.subentry_id
        )
        payload[SECTION_GUEST_MODE] = await guest_mode_manager.async_backup_data()
    return DecodedPrivateDocument(document) if mode == "custom" else document


def _is_redacted_placeholder(value: Any) -> bool:
    """Return whether a value is one of our explicit redaction placeholders."""
    return is_redacted_secret(value)


def _normalize_redaction_placeholders(value: Any) -> Any:
    """Use one explicit placeholder in newly created transfer documents."""
    if is_literal_text(value) or is_redacted_url(value):
        return deepcopy(value)
    if _is_redacted_placeholder(value):
        return dict(REDACTED_SECRET_SENTINEL)
    if isinstance(value, list):
        return [_normalize_redaction_placeholders(item) for item in value]
    if isinstance(value, dict):
        return {
            key: _normalize_redaction_placeholders(item) for key, item in value.items()
        }
    return value


def redact_transfer_document(document: dict[str, Any]) -> dict[str, Any]:
    """Redact secret-bearing setup sections without altering private state content."""
    result = deepcopy(document)
    sections = result.get("sections")
    if isinstance(sections, dict):
        if SECTION_CONFIGURATION in sections:
            sections[SECTION_CONFIGURATION] = _normalize_redaction_placeholders(
                redact_secrets(sections[SECTION_CONFIGURATION])
            )
        if SECTION_REQUEST_RULES in sections:
            sections[SECTION_REQUEST_RULES] = _normalize_redaction_placeholders(
                redact_secrets(sections[SECTION_REQUEST_RULES])
            )
    return result


def finalize_setup_export(
    document: dict[str, Any], *, reviewed: bool = False
) -> dict[str, Any]:
    """Serialize a lightweight shareable setup document."""
    try:
        check_redaction_budget(document, backup.MAX_LEGACY_EXPORT_BYTES)
        if document.get("version") == SHARING_VERSION:
            sections, security = project_sharing_sections(
                document["sections"], reviewed=reviewed
            )
            redacted = {
                "format": TRANSFER_FORMAT,
                "version": SHARING_VERSION,
                "mode": "setup",
                "created_at": document["created_at"],
                "integration_version": document["integration_version"],
                "agent": {
                    "title": "Shared conversation agent",
                    "source_entry_id": "shared-entry",
                    "source_subentry_id": "shared-agent",
                },
                "sections": sections,
                "security": security,
            }
        else:
            # Explicit legacy v1 semantics retained for old clients/fixtures.
            redacted = redact_transfer_document(document)
        serialized = bounded_json_dumps(redacted, backup.MAX_LEGACY_EXPORT_BYTES)
    except JSONBudgetExceeded as err:
        raise backup.BackupError(
            "This setup export exceeds the 16 MB safety limit; use a custom backup instead"
        ) from err
    title = validate_agent_title(redacted["agent"]["title"])
    date = str(redacted["created_at"])[:10]
    return {
        "document": redacted,
        "json": serialized,
        "filename": f"{_safe_title(title)}-shareable-setup-{date}.json",
        "security": redacted.get("security"),
        "mode": "setup",
        "sections": list(SECTION_ORDER[:2]),
    }


async def async_create_setup_export(
    hass: HomeAssistant, entry: Any, subentry: Any, *, reviewed: bool = False
) -> dict[str, Any]:
    """Build a lightweight, redacted setup-sharing document."""
    from .agent_maintenance import get_agent_maintenance_gate

    gate = get_agent_maintenance_gate(hass, entry.entry_id, subentry.subentry_id)
    async with gate.exclusive():
        document = await async_collect_transfer_snapshot(
            hass, entry, subentry, mode="setup"
        )
    return await asyncio.to_thread(finalize_setup_export, document, reviewed=reviewed)


def _secret_path(path: tuple[Any, ...]) -> str:
    if not path:
        return "value"
    text = ""
    for item in path:
        if isinstance(item, int):
            text += f"[{item}]"
        else:
            text += ("." if text else "") + str(item)
    return text


def _collect_secret_paths(value: Any, path: tuple[Any, ...] = ()) -> list[str]:
    if not path:
        value = decoded_function_fields(value)
    if is_literal_text(value):
        return []
    if _is_redacted_placeholder(value):
        return [_secret_path(path)]
    if isinstance(value, list):
        result: list[str] = []
        for index, item in enumerate(value):
            result.extend(_collect_secret_paths(item, (*path, index)))
        return result
    if isinstance(value, dict):
        result = []
        for key, item in value.items():
            result.extend(_collect_secret_paths(item, (*path, key)))
        return result
    return []


def _list_item_identity(item: Any) -> Any:
    """Use explicit IDs, or the stable spec name of a Function Tool."""
    if isinstance(item, Mapping):
        if "id" in item:
            return ("id", item["id"])
        spec = item.get("spec")
        if isinstance(spec, Mapping) and "name" in spec:
            return ("spec.name", spec["name"])
    return None


def _compatible_secret_context(
    value: Any, fallback: Any, *, ordered: bool = False
) -> bool:
    """Require identical visible structure; only secret scalar leaves may differ.

    A marker cannot hide a container (and therefore a destination). Identical
    public lists can contain duplicates; lists with secrets may reorder only
    when their items have unique, one-to-one safe matches.
    """
    if is_literal_text(value):
        return bool(value[LITERAL_TEXT_KEY] == fallback)
    if is_redacted_url(value):
        return (
            isinstance(fallback, str)
            and None not in value.values()
            and redact_http_url(fallback) == value
        )
    if _is_redacted_placeholder(value):
        return (
            fallback is not _ABSENT
            and not isinstance(fallback, (Mapping, list))
            and not _is_redacted_placeholder(fallback)
        )
    if isinstance(value, dict):
        return (
            isinstance(fallback, Mapping)
            and value.keys() == fallback.keys()
            and all(
                _compatible_secret_context(
                    item, fallback[key], ordered=key == "sequence"
                )
                for key, item in value.items()
            )
        )
    if isinstance(value, list):
        if not isinstance(fallback, list) or len(value) != len(fallback):
            return False
        same_order = all(
            _compatible_secret_context(item, candidate)
            for item, candidate in zip(value, fallback, strict=True)
        )
        if ordered:
            return same_order
        # Public values are compared in full, so repeated entries introduce no
        # uncertainty about which local credential a marker can recover.
        if same_order and not _collect_secret_paths(value):
            return True
        matches = [_fallback_list_index(item, fallback) for item in value]
        return None not in matches and len(set(matches)) == len(matches)
    if type(value) is not type(fallback):
        return False
    # YAML's .nan is a typed scalar whose IEEE equality is false even against
    # itself. Treat matching NaN scalars as equivalent without weakening the
    # structural checks used to recover redacted credentials.
    if isinstance(value, float) and math.isnan(value):
        return math.isnan(fallback)
    return bool(value == fallback)


def _fallback_list_index(item: Any, fallback: list[Any]) -> int | None:
    """Select a unique compatible item, never infer authority from its position."""
    identity = _list_item_identity(item)
    if identity is not None and (not isinstance(identity[1], str) or not identity[1]):
        return None
    candidates = [
        index
        for index, candidate in enumerate(fallback)
        if _list_item_identity(candidate) == identity
    ]
    # Duplicate stable identities are invalid even if their public fields differ.
    if identity is not None and len(candidates) != 1:
        return None
    matches = [
        index
        for index in candidates
        if _compatible_secret_context(item, fallback[index])
    ]
    return matches[0] if len(matches) == 1 else None


def _restore_with_fallback(
    value: Any,
    fallback: Any,
    *,
    path: tuple[Any, ...] = (),
    preserved: list[str],
    missing: list[str],
) -> Any:
    if is_literal_text(value):
        return value[LITERAL_TEXT_KEY]
    if _is_redacted_placeholder(value):
        label = _secret_path(path)
        if _compatible_secret_context(value, fallback):
            preserved.append(label)
            return deepcopy(fallback)
        missing.append(label)
        return _DROP
    if isinstance(value, list):
        restored: list[Any] = []
        local_items = fallback if isinstance(fallback, list) else []
        matches = [_fallback_list_index(item, local_items) for item in value]
        if path and path[-1] == "sequence":
            matches = (
                list(range(len(value)))
                if _compatible_secret_context(value, local_items, ordered=True)
                else [None] * len(value)
            )
        for index, item in enumerate(value):
            match = matches[index]
            candidate = (
                local_items[match]
                if match is not None and matches.count(match) == 1
                else _ABSENT
            )
            child = _restore_with_fallback(
                item,
                candidate,
                path=(*path, index),
                preserved=preserved,
                missing=missing,
            )
            if child is not _DROP:
                restored.append(child)
        return restored
    if isinstance(value, dict):
        restored_mapping: dict[Any, Any] = {}
        fallback_mapping = fallback if isinstance(fallback, Mapping) else {}
        for key, item in value.items():
            candidate = fallback_mapping.get(key, _ABSENT)
            child = _restore_with_fallback(
                item,
                candidate,
                path=(*path, key),
                preserved=preserved,
                missing=missing,
            )
            if child is not _DROP:
                restored_mapping[key] = child
        return restored_mapping
    return deepcopy(value)


def _request_rule_secret_context(value: Any) -> Any:
    """Exclude recomputed action metadata from credential-context matching."""
    if not isinstance(value, Mapping) or not isinstance(value.get("rules"), list):
        return value
    rules = []
    for rule in value["rules"]:
        if (
            isinstance(rule, Mapping)
            and rule.get("action_type") == "local_action"
            and isinstance(rule.get("action"), Mapping)
        ):
            rule = {
                **rule,
                "action": {
                    key: item
                    for key, item in rule["action"].items()
                    if key != "canonical_signature"
                },
            }
        rules.append(rule)
    return {**value, "rules": rules}


def _restore_section_secrets(
    value: Any, fallback: Any
) -> tuple[Any, tuple[str, ...], tuple[str, ...]]:
    preserved: list[str] = []
    missing: list[str] = []
    restored = _restore_with_fallback(
        decoded_function_fields(value),
        decoded_function_fields(fallback),
        preserved=preserved,
        missing=missing,
    )
    return restored, tuple(preserved), tuple(missing)


def _validate_metadata(value: Mapping[str, Any]) -> tuple[str, str, str]:
    created_at = value.get("created_at")
    integration_version = value.get("integration_version")
    if (
        not isinstance(created_at, str)
        or dt_util.parse_datetime(created_at) is None
        or not isinstance(integration_version, str)
        or not integration_version
    ):
        raise backup.BackupError("The transfer metadata is invalid")
    agent = value.get("agent")
    if not isinstance(agent, Mapping) or set(agent) != {
        "title",
        "source_entry_id",
        "source_subentry_id",
    }:
        raise backup.BackupError("The transfer agent metadata is invalid")
    try:
        title = validate_agent_title(agent.get("title"))
    except HomeAssistantError as err:
        raise backup.BackupError("The transferred agent name is invalid") from err
    if not isinstance(agent.get("source_entry_id"), str) or not isinstance(
        agent.get("source_subentry_id"), str
    ):
        raise backup.BackupError("The transfer agent identity is invalid")
    return title, created_at, integration_version


def _validate_transfer_document(
    value: Mapping[str, Any],
    target_agent_id: str,
    selected: Iterable[str] | None = None,
    *,
    validated_categories: dict[str, Any] | None = None,
) -> PreparedTransfer:
    expected = {
        "format",
        "version",
        "mode",
        "created_at",
        "integration_version",
        "agent",
        "sections",
    }
    private = (
        value.get("version") == PRIVATE_TRANSFER_VERSION
        and value.get("mode") == "custom"
    )
    if (value.get("version") == PRIVATE_TRANSFER_VERSION and not private) or (
        value.get("version") == SHARING_VERSION and value.get("mode") != "setup"
    ):
        raise backup.BackupError("This transfer uses an unsupported format version")
    if private and "provider_connection" in value:
        expected.add("provider_connection")
    if value.get("version") == SHARING_VERSION and value.get("mode") == "setup":
        expected.add("security")
    if set(value) != expected:
        raise backup.BackupError("The transfer file is incomplete or corrupted")
    if value.get("version") not in {
        TRANSFER_VERSION,
        SHARING_VERSION,
        PRIVATE_TRANSFER_VERSION,
    }:
        version = value.get("version")
        if isinstance(version, int) and version > TRANSFER_VERSION:
            raise backup.BackupError(
                "This transfer was created using a newer unsupported format"
            )
        raise backup.BackupError("This transfer uses an unsupported format version")
    mode = value.get("mode")
    if mode not in {"setup", "custom"}:
        raise backup.BackupError("The transfer mode is invalid")
    title, created_at, integration_version = _validate_metadata(value)
    raw_sections = value.get("sections")
    if not isinstance(raw_sections, Mapping):
        raise backup.BackupError("The transfer sections are invalid")
    available = validate_section_selection(raw_sections.keys())
    if mode == "setup" and available != SETUP_SECTIONS:
        raise backup.BackupError("The shareable setup sections are incomplete")
    validating = (
        validate_section_selection(selected, allowed=available, default=available)
        if selected != frozenset()
        else frozenset()
    )

    prepared = PreparedTransfer(
        source_kind="portable_transfer" if mode == "setup" else "custom_backup",
        mode=mode,
        title=title,
        available_sections=available,
        created_at=created_at,
        integration_version=integration_version,
        security=value.get("security"),
        private=private,
        provider_connection=deepcopy(value.get("provider_connection"))
        if private
        else None,
    )
    redacted: list[str] = []
    try:
        if SECTION_CONFIGURATION in validating:
            raw = raw_sections[SECTION_CONFIGURATION]
            prepared.raw_configuration = None if private else deepcopy(raw)
            if not private:
                redacted.extend(
                    f"configuration.{path}" for path in _collect_secret_paths(raw)
                )
            restored = deepcopy(raw) if private else restore_redacted_secrets(raw)
            if not isinstance(restored, dict):
                raise ValueError("configuration must be an object")
            if private:
                backup.private_configuration_snapshot(restored)
            prepared.config = (
                restored
                if private or _collect_secret_paths(raw)
                else backup.recoverable_configuration_snapshot(restored)
            )
        if SECTION_REQUEST_RULES in validating:
            raw = raw_sections[SECTION_REQUEST_RULES]
            prepared.raw_request_rules = None if private else deepcopy(raw)
            if not private:
                redacted.extend(
                    f"request_rules.{path}" for path in _collect_secret_paths(raw)
                )
            prepared.request_rules = (
                None
                if not private and _collect_secret_paths(raw)
                else RequestRules.validate_backup_data(
                    raw if private else restore_redacted_secrets(raw)
                )
            )
        categories = validated_categories
        if categories is None:
            categories = backup._validate_data_categories(
                _data_categories(raw_sections, validating), target_agent_id
            )
        if SECTION_PERSISTENT_MEMORY in validating:
            prepared.memories = categories["memories"]
        if SECTION_TEMPORARY_MEMORY in validating:
            prepared.temporary_memories = categories["temporary_memories"]
        if SECTION_KNOWLEDGE in validating:
            prepared.knowledge = categories["knowledge"]
        if SECTION_CONVERSATION_ARCHIVE in validating:
            prepared.archive_sessions, prepared.archive_turns, _ = categories["archive"]
        if SECTION_USAGE in validating:
            (
                prepared.usage_totals,
                prepared.usage_daily,
                prepared.usage_requests,
                prepared.usage_runs,
            ) = categories["usage"]
        if SECTION_GUEST_MODE in validating:
            prepared.guest_mode_schedule = GuestModeManager.validate_backup_data(
                raw_sections[SECTION_GUEST_MODE]
            )
    except (HomeAssistantError, TypeError, ValueError) as err:
        raise backup.BackupError(
            f"The transfer is incomplete or corrupted: {err}"
        ) from err
    prepared.redacted_sensitive_fields = tuple(redacted)
    return prepared


def _inspect_legacy_setup(
    value: Mapping[str, Any], *, inspect_only: bool = False
) -> PreparedTransfer:
    if value.get("version") not in {AGENT_CONFIG_EXPORT_VERSION, SHARING_VERSION}:
        raise backup.BackupError("This setup export uses an unsupported version")
    unknown = set(value) - {"schema", "version", "title", "config", "security"}
    if unknown:
        raise backup.BackupError(
            "The setup export contains unknown fields: " + ", ".join(sorted(unknown))
        )
    raw_config = value.get("config")
    redacted = tuple(
        f"configuration.{path}" for path in _collect_secret_paths(raw_config)
    )
    try:
        restored = restore_redacted_secrets(raw_config)
        if not isinstance(restored, dict):
            raise ValueError("configuration must be an object")
        config = (
            restored
            if inspect_only or redacted
            else backup.recoverable_configuration_snapshot(restored)
        )
        title = validate_agent_title(
            value.get("title"), default="Imported conversation agent"
        )
    except (HomeAssistantError, TypeError, ValueError) as err:
        raise backup.BackupError(f"The setup export is invalid: {err}") from err
    return PreparedTransfer(
        source_kind="legacy_setup",
        mode="setup",
        title=title,
        available_sections=frozenset({SECTION_CONFIGURATION}),
        created_at=None,
        integration_version=None,
        config=config,
        raw_configuration=deepcopy(raw_config),
        redacted_sensitive_fields=redacted,
        security=value.get("security"),
    )


def _inspect_full_backup(
    value: Mapping[str, Any],
    target_agent_id: str,
    selected: Iterable[str] | None = None,
    *,
    validated_categories: dict[str, Any] | None = None,
) -> PreparedTransfer:
    header = backup._inspect_decoded_backup(
        dict(value), target_agent_id, header_only=True
    )
    private = value.get("version") == PRIVATE_BACKUP_VERSION
    if private:
        value = decode_document(value)
    sections = {
        SECTION_CONFIGURATION: value["agent"]["config"],
        SECTION_PERSISTENT_MEMORY: value["memories"],
        SECTION_TEMPORARY_MEMORY: value["temporary_memories"],
        SECTION_KNOWLEDGE: value["knowledge"],
        SECTION_CONVERSATION_ARCHIVE: value["archive"],
        SECTION_USAGE: value["usage"],
    }
    for key in (SECTION_REQUEST_RULES, SECTION_GUEST_MODE):
        if key in value:
            sections[key] = value[key]
    portable = {
        "format": TRANSFER_FORMAT,
        "version": PRIVATE_TRANSFER_VERSION if private else TRANSFER_VERSION,
        "mode": "custom",
        "created_at": header.created_at,
        "integration_version": header.integration_version,
        "agent": {key: item for key, item in value["agent"].items() if key != "config"},
        "sections": sections,
        **(
            {"provider_connection": value["provider_connection"]}
            if "provider_connection" in value
            else {}
        ),
    }
    prepared = _validate_transfer_document(
        portable, target_agent_id, selected, validated_categories=validated_categories
    )
    prepared.source_kind, prepared.mode = "full_backup", "full"
    return prepared


def inspect_transfer(
    value: Any,
    target_agent_id: str,
    *,
    sections: Iterable[str] | None = None,
    inspect_only: bool = False,
) -> PreparedTransfer:
    """Classify and fully validate portable, custom, legacy, or full backup input."""
    if isinstance(value, PreparedTransfer):
        return value
    value = _decode_transfer_input(value)
    return _inspect_decoded_transfer(
        value, target_agent_id, sections=sections, inspect_only=inspect_only
    )


def _decode_transfer_input(value: Any) -> Mapping[str, Any]:
    """Bound and decode an external document without HA schema validation."""
    if isinstance(value, str):
        try:
            check_utf8_budget(value, backup.MAX_BACKUP_BYTES)
        except JSONBudgetExceeded as err:
            raise backup.BackupError(
                "The transfer exceeds the 128 MB safety limit"
            ) from err
        try:
            value = json.loads(value)
        except json.JSONDecodeError as err:
            raise backup.BackupError("The transfer is not valid JSON") from err
    if not isinstance(value, Mapping):
        raise backup.BackupError(
            "This file is not an Extended OpenAI Conversation transfer"
        )
    if value.get("format") == backup.BACKUP_FORMAT:
        value = backup._decode_backup_document(value, backup.MAX_BACKUP_BYTES)
    return decode_document(value) if is_private_document(value) else dict(value)


async def async_inspect_transfer(
    value: Any, target_agent_id: str, *, sections: Iterable[str] | None = None
) -> PreparedTransfer:
    """Inspect external transfers without running bulk category work on HA's loop."""
    if isinstance(value, PreparedTransfer):
        return value
    document = await asyncio.to_thread(_decode_transfer_input, value)
    return await async_inspect_decoded_transfer(
        document, target_agent_id, sections=sections
    )


async def async_inspect_decoded_transfer(
    value: Mapping[str, Any],
    target_agent_id: str,
    *,
    sections: Iterable[str] | None = None,
    inspect_only: bool = False,
) -> PreparedTransfer:
    """Validate HA schemas on-loop, but bulk data categories in a worker."""
    if is_private_document(value):
        value = await asyncio.to_thread(decode_document, value)
    header = _inspect_decoded_transfer(value, target_agent_id, inspect_only=True)
    if inspect_only:
        return header
    selected = validate_section_selection(
        sections, allowed=header.available_sections, default=header.available_sections
    )
    raw_categories = (
        {
            key: value[key]
            for section, key in _DATA_CATEGORY_KEYS.items()
            if section in selected
        }
        if value.get("format") == backup.BACKUP_FORMAT
        else _data_categories(value.get("sections", {}), selected)
    )
    try:
        categories = await asyncio.to_thread(
            backup._validate_data_categories, raw_categories, target_agent_id
        )
    except (TypeError, ValueError) as err:
        raise backup.BackupError(
            f"The transfer is incomplete or corrupted: {err}"
        ) from err
    return _inspect_decoded_transfer(
        value, target_agent_id, sections=selected, validated_categories=categories
    )


_DATA_CATEGORY_KEYS = {
    SECTION_PERSISTENT_MEMORY: "memories",
    SECTION_TEMPORARY_MEMORY: "temporary_memories",
    SECTION_KNOWLEDGE: "knowledge",
    SECTION_CONVERSATION_ARCHIVE: "archive",
    SECTION_USAGE: "usage",
}


def _data_categories(
    sections: Mapping[str, Any], selected: Iterable[str]
) -> dict[str, Any]:
    return {
        key: sections[section]
        for section, key in _DATA_CATEGORY_KEYS.items()
        if section in selected
    }


def _inspect_decoded_transfer(
    value: Mapping[str, Any],
    target_agent_id: str,
    *,
    sections: Iterable[str] | None = None,
    inspect_only: bool = False,
    validated_categories: dict[str, Any] | None = None,
) -> PreparedTransfer:
    """Validate HA-aware schemas after a bounded, context-free transport read.

    File readers already verify actual bytes, decompression and manifest hashes.
    Re-encoding their decoded document on HA's loop would block unrelated work.
    """
    if value.get("format") == TRANSFER_FORMAT:
        if value.get("version") == PRIVATE_TRANSFER_VERSION:
            value = decode_document(value)
        return _validate_transfer_document(
            value,
            target_agent_id,
            frozenset() if inspect_only else sections,
            validated_categories=validated_categories,
        )
    if value.get("format") == backup.BACKUP_FORMAT:
        return _inspect_full_backup(
            value,
            target_agent_id,
            frozenset() if inspect_only else sections,
            validated_categories=validated_categories,
        )
    if value.get("schema") == LEGACY_AGENT_SCHEMA:
        return _inspect_legacy_setup(
            value,
            inspect_only=inspect_only
            or (sections is not None and SECTION_CONFIGURATION not in sections),
        )
    raise backup.BackupError(
        "This file is not a recognised Extended OpenAI Conversation export or backup"
    )


async def _async_validate_request_rule_function_dependencies(
    hass: HomeAssistant,
    request_rules: Mapping[str, Any],
    config: Mapping[str, Any],
) -> None:
    """Apply canonical Request Rule Function validation to one combined target."""
    quarantined_names: set[str] = set()
    try:
        tools = configured_function_tools_from_data(config)
    except HomeAssistantError, yaml.YAMLError, TypeError, ValueError:
        from .management_function_repair import (
            function_tools_issue,
            isolated_function_tools,
        )

        tools, issue = function_tools_issue(dict(config))
        if issue is None:
            raise
        _valid, invalid, _isolated_issue = isolated_function_tools(dict(config))
        quarantined_names = {
            str(item["name"])
            for item in invalid
            if isinstance(item.get("name"), str) and item["name"]
        }
    for rule in request_rules.get("rules", []):
        if not isinstance(rule, Mapping):
            continue
        try:
            await async_validate_request_rule_functions(
                hass,
                rule,
                tools,
                quarantined_names=quarantined_names,
                allow_disabled=True,
            )
        except (HomeAssistantError, ValueError) as err:
            label = rule.get("name", rule.get("id", "unnamed"))
            raise backup.BackupError(
                f"Request Rule `{label}` has an invalid Function Tool action: {err}"
            ) from err


def _copy_transfer_categories(prepared: PreparedTransfer) -> PreparedTransfer:
    """Detach data-only categories; never copy HA-aware configuration in a worker."""
    fields = (
        "memories",
        "temporary_memories",
        "knowledge",
        "archive_sessions",
        "archive_turns",
        "usage_totals",
        "usage_daily",
        "usage_requests",
        "usage_runs",
        "guest_mode_schedule",
    )
    copied = deepcopy(tuple(getattr(prepared, name) for name in fields))
    return replace(prepared, **dict(zip(fields, copied, strict=True)))


def _prepared_restore_from_selection(
    current: backup.PreparedRestore,
    imported: PreparedTransfer,
    selected: frozenset[str],
    *,
    copy_categories: bool = True,
) -> tuple[backup.PreparedRestore, tuple[str, ...], tuple[str, ...]]:
    """Overlay selected replacement sections on one complete current snapshot."""
    copy_category = deepcopy if copy_categories else lambda value: value
    preserved: list[str] = []
    missing: list[str] = []

    title = current.title
    config = current.config
    if SECTION_CONFIGURATION in selected:
        title = imported.title
        if imported.raw_configuration is not None:
            raw, kept, absent = _restore_section_secrets(
                imported.raw_configuration,
                backup.recoverable_configuration_snapshot(current.config),
            )
            preserved.extend(f"configuration.{path}" for path in kept)
            missing.extend(f"configuration.{path}" for path in absent)
            if absent:
                raise backup.BackupError(
                    "Cannot safely restore unavailable secrets: "
                    + ", ".join(absent[:50])
                )
            if not isinstance(raw, dict):
                raise backup.BackupError("Transferred configuration is invalid")
            config = backup.recoverable_configuration_snapshot(raw)
        elif imported.config is not None:
            config = deepcopy(imported.config)
        else:
            raise backup.BackupError("Transferred configuration is unavailable")
    else:
        # Unselected configuration is authoritative private source. Validate a
        # separate projection without rewriting credentials, YAML or omissions.
        backup.private_configuration_snapshot(current.config)
        config = (
            deepcopy(current.config)
            if current.authoritative_config
            else backup.private_configuration_snapshot(current.config)
        )

    rules = current.request_rules
    if SECTION_REQUEST_RULES in selected:
        if imported.raw_request_rules is not None:
            raw, kept, absent = _restore_section_secrets(
                _request_rule_secret_context(imported.raw_request_rules),
                _request_rule_secret_context(current.request_rules),
            )
            preserved.extend(f"request_rules.{path}" for path in kept)
            missing.extend(f"request_rules.{path}" for path in absent)
            if absent:
                raise backup.BackupError(
                    "Cannot safely restore unavailable secrets: "
                    + ", ".join(absent[:50])
                )
            rules = RequestRules.validate_backup_data(raw)
        elif imported.request_rules is not None:
            rules = deepcopy(imported.request_rules)
        else:
            raise backup.BackupError("Transferred Request Rules are unavailable")

    target = backup.PreparedRestore(
        title=title,
        config=config,
        memories=(
            copy_category(imported.memories)
            if SECTION_PERSISTENT_MEMORY in selected and imported.memories is not None
            else current.memories
        ),
        temporary_memories=(
            copy_category(imported.temporary_memories)
            if SECTION_TEMPORARY_MEMORY in selected
            and imported.temporary_memories is not None
            else current.temporary_memories
        ),
        knowledge=(
            copy_category(imported.knowledge)
            if SECTION_KNOWLEDGE in selected and imported.knowledge is not None
            else current.knowledge
        ),
        archive_sessions=(
            copy_category(imported.archive_sessions)
            if SECTION_CONVERSATION_ARCHIVE in selected
            and imported.archive_sessions is not None
            else current.archive_sessions
        ),
        archive_turns=(
            copy_category(imported.archive_turns)
            if SECTION_CONVERSATION_ARCHIVE in selected
            and imported.archive_turns is not None
            else current.archive_turns
        ),
        usage_totals=(
            copy_category(imported.usage_totals)
            if SECTION_USAGE in selected and imported.usage_totals is not None
            else current.usage_totals
        ),
        usage_daily=(
            copy_category(imported.usage_daily)
            if SECTION_USAGE in selected and imported.usage_daily is not None
            else current.usage_daily
        ),
        usage_requests=(
            copy_category(imported.usage_requests)
            if SECTION_USAGE in selected and imported.usage_requests is not None
            else current.usage_requests
        ),
        usage_runs=(
            copy_category(imported.usage_runs)
            if SECTION_USAGE in selected and imported.usage_runs is not None
            else current.usage_runs
        ),
        guest_mode_schedule=(
            copy_category(imported.guest_mode_schedule)
            if SECTION_GUEST_MODE in selected
            else current.guest_mode_schedule
        ),
        request_rules=rules,
        created_at=imported.created_at or current.created_at,
        integration_version=imported.integration_version or current.integration_version,
        archive_active=(
            None
            if SECTION_CONVERSATION_ARCHIVE in selected
            else deepcopy(current.archive_active)
        ),
        authoritative_config=True,
    )
    return target, tuple(preserved), tuple(missing)


async def _current_snapshot(
    hass: HomeAssistant, entry: Any, subentry: Any
) -> backup.PreparedRestore:
    """Read durable current state for selective planning without mutating it."""
    try:
        from .restore_recovery import _durable_managers

        managers = await _durable_managers(hass, entry.entry_id, subentry.subentry_id)
    except ImportError:
        managers = await backup._managers(hass, entry.entry_id, subentry.subentry_id)
    return await backup._snapshot_for_restore(managers, subentry)


async def async_materialize_restore(
    hass: HomeAssistant,
    entry: Any,
    subentry: Any,
    imported: PreparedTransfer | Any,
    *,
    sections: Iterable[str] | None = None,
    current_snapshot: backup.PreparedRestore | None = None,
    provider_connection: Mapping[str, Any] | None = None,
) -> tuple[backup.PreparedRestore, dict[str, Any]]:
    """Build and validate a complete restore target from a selective transfer."""
    prepared = await async_inspect_transfer(
        imported, subentry.subentry_id, sections=sections
    )
    selected = validate_section_selection(
        sections,
        allowed=prepared.available_sections,
        default=prepared.available_sections,
    )
    current = (
        current_snapshot
        if current_snapshot is not None
        else await _current_snapshot(hass, entry, subentry)
    )
    prepared = await asyncio.to_thread(_copy_transfer_categories, prepared)
    target, preserved, missing = _prepared_restore_from_selection(
        current, prepared, selected, copy_categories=False
    )
    if missing:
        raise backup.BackupError(
            "Cannot safely restore unavailable secrets: " + ", ".join(missing[:50])
        )
    from .request_rules import (
        validate_routed_request_options,
        validate_saved_model_routes,
    )

    try:
        provider_data = (
            provider_connection
            if provider_connection is not None
            else getattr(entry, "data", {})
        )
        validate_routed_request_options(target.config, provider_data)
        validate_saved_model_routes(
            target.request_rules.get("rules", []),
            target.config,
            provider_data,
        )
    except HomeAssistantError as err:
        raise backup.BackupError(
            f"Restored request is incompatible with the destination provider: {err}"
        ) from err
    await _async_validate_request_rule_function_dependencies(
        hass, target.request_rules, target.config
    )
    preview = prepared.summary()
    preview.update(
        {
            "selected_sections": [
                section for section in SECTION_ORDER if section in selected
            ],
            "preserved_sensitive_fields": list(preserved[:50]),
            "preserved_sensitive_field_count": len(preserved),
            "missing_sensitive_fields": list(missing[:50]),
            "missing_sensitive_field_count": len(missing),
            "target_title": target.title,
        }
    )
    return target, preview


async def async_restore_transfer(
    hass: HomeAssistant,
    entry: Any,
    subentry: Any,
    imported: PreparedTransfer | Any,
    *,
    sections: Iterable[str] | None = None,
    precondition: Callable[[], Awaitable[None]] | None = None,
    provider_connection: dict[str, Any] | None = None,
    expected_provider_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Preserve unselected state and commit under one shielded exclusive lease."""
    from .agent_maintenance import (
        _async_run_exclusive_operation,
        get_agent_maintenance_gate,
    )
    from .restore_recovery import (
        async_finish_restore_reload,
        async_restore_backup_recoverably,
    )

    async def restore_exclusively_owned() -> dict[str, Any]:
        if precondition is not None:
            await precondition()
        if (
            provider_connection is not None
            and dict(entry.data) != expected_provider_data
        ):
            raise backup.BackupError(
                "The provider connection changed after preview; preview again"
            )
        provider_options: dict[str, Any] = (
            {"provider_connection": provider_connection}
            if provider_connection is not None
            else {}
        )
        target, preview = await async_materialize_restore(
            hass,
            entry,
            subentry,
            imported,
            sections=sections,
            **provider_options,
        )
        # Exclusivity already covers the destination snapshot. Call the journaled
        # inner restore directly: the public backup wrapper would reacquire this
        # non-reentrant gate. Lock order remains maintenance gate -> backup lock.
        result = await async_restore_backup_recoverably(hass, entry, subentry, target)
        if provider_connection is not None:
            from .provider_credentials import restore_private_provider_connection

            assert expected_provider_data is not None
            try:
                restore_private_provider_connection(
                    hass,
                    entry,
                    provider_connection,
                    expected_provider_data,
                    confirmed=True,
                )
            except HomeAssistantError:
                result["provider_connection_restored"] = False
                result["provider_connection_message"] = (
                    "Agent data restored; provider connection was not changed. Import and preview again."
                )
            else:
                result["provider_connection_restored"] = True
        return {**result, "transfer": preview}

    gate = get_agent_maintenance_gate(hass, entry.entry_id, subentry.subentry_id)
    result = await _async_run_exclusive_operation(gate, restore_exclusively_owned)
    await async_finish_restore_reload(hass, entry, subentry)
    return result


def inspection_for_frontend(prepared: PreparedTransfer) -> dict[str, Any]:
    """Return stable inspection metadata before a target-specific preview."""
    return {
        "valid": True,
        "title": prepared.title,
        "source_kind": prepared.source_kind,
        "mode": prepared.mode,
        "available_sections": [
            section
            for section in SECTION_ORDER
            if section in prepared.available_sections
        ],
        "summary": prepared.summary(),
        "can_create_new_agent": prepared.available_sections.issubset(SETUP_SECTIONS)
        and SECTION_CONFIGURATION in prepared.available_sections,
    }
