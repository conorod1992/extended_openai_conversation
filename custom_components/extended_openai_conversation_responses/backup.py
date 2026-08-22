"""Versioned, per-agent disaster-recovery backups."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import logging
from pathlib import Path
import re
from typing import Any, cast

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from .agent_config import (
    agent_config_snapshot,
    normalize_agent_config,
    preserve_legacy_guest_policy,
)
from .const import (
    CONF_USAGE_REQUEST_RETENTION_DAYS,
    CONF_USAGE_RUN_RETENTION_DAYS,
    DEFAULT_USAGE_REQUEST_RETENTION_DAYS,
    DEFAULT_USAGE_RUN_RETENTION_DAYS,
    DOMAIN,
)
from .conversation_archive import (
    ArchiveSession,
    ArchiveTurn,
    ConversationArchive,
    async_get_archive,
)
from .guest_mode import GuestModeManager, GuestModeSchedule, async_get_guest_mode
from .knowledge import KnowledgeLibrary, KnowledgeSource, async_get_knowledge
from .memory import MemoryRecord, PersistentMemory, async_get_memory
from .request_rules import RequestRules, async_get_request_rules
from .temporary_memory import (
    TemporaryMemory,
    TemporaryMemoryRecord,
    async_get_temporary_memory,
)
from .usage import UsageManager, UsageRequest, UsageRun, UsageTotals, async_get_usage

BACKUP_FORMAT = "extended_openai_conversation_backup"
BACKUP_VERSION = 3
MAX_BACKUP_BYTES = 16 * 1024 * 1024
_BACKUP_LOCKS = f"{DOMAIN}.backup_locks"
_LOGGER = logging.getLogger(__name__)
_SECRET_KEY = re.compile(
    r"(?:^|[_-])(?:api_?key|password|passwd|secret|token|authorization)(?:$|[_-])"
    r"|(?:apiKey|clientSecret|accessToken|refreshToken)$",
    re.IGNORECASE,
)
_LIKELY_SECRET = re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b")


class BackupError(HomeAssistantError):
    """A safe, user-facing backup validation or restore error."""


@dataclass(slots=True)
class PreparedRestore:
    """Fully validated replacement data."""

    title: str
    config: dict[str, Any]
    memories: list[MemoryRecord]
    temporary_memories: list[TemporaryMemoryRecord]
    knowledge: list[KnowledgeSource]
    archive_sessions: list[ArchiveSession]
    archive_turns: list[ArchiveTurn]
    usage_totals: UsageTotals
    usage_daily: dict[str, dict[str, Any]]
    usage_requests: list[UsageRequest]
    usage_runs: list[UsageRun]
    guest_mode_schedule: GuestModeSchedule | None
    request_rules: dict[str, Any]
    created_at: str
    integration_version: str

    def summary(self) -> dict[str, Any]:
        return {
            "configuration": True,
            "persistent_memories": len(self.memories),
            "temporary_memories": len(self.temporary_memories),
            "knowledge_sources": len(self.knowledge),
            "archive_sessions": len(self.archive_sessions),
            "archive_turns": len(self.archive_turns),
            "usage_requests": len(self.usage_requests),
            "usage_runs": len(self.usage_runs),
            "guest_mode_scheduled": self.guest_mode_schedule is not None,
            "request_rules": len(self.request_rules["rules"]),
            "created_at": self.created_at,
            "integration_version": self.integration_version,
        }


def _integration_version() -> str:
    manifest = json.loads((Path(__file__).parent / "manifest.json").read_text("utf-8"))
    return str(manifest["version"])


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
        if not schema and _SECRET_KEY.search(str(key)):
            continue
        result[key] = _safe_configuration(item, schema=child_schema)
    return result


def _backup_lock(hass: HomeAssistant, entry_id: str, subentry_id: str) -> asyncio.Lock:
    locks = cast(
        dict[tuple[str, str], asyncio.Lock], hass.data.setdefault(_BACKUP_LOCKS, {})
    )
    return locks.setdefault((entry_id, subentry_id), asyncio.Lock())


async def async_create_backup(
    hass: HomeAssistant, entry: Any, subentry: Any
) -> dict[str, Any]:
    """Collect a private, JSON-compatible snapshot of one agent."""
    async with _backup_lock(hass, entry.entry_id, subentry.subentry_id):
        (
            memory,
            temporary,
            knowledge,
            archive,
            usage,
            guest_mode,
            request_rules,
        ) = await _managers(hass, entry.entry_id, subentry.subentry_id)
        config_snapshot = preserve_legacy_guest_policy(
            dict(subentry.data), agent_config_snapshot(subentry.data)
        )
        document = {
            "format": BACKUP_FORMAT,
            "version": BACKUP_VERSION,
            "created_at": dt_util.utcnow().isoformat(),
            "integration_version": _integration_version(),
            "agent": {
                "title": subentry.title,
                "source_entry_id": entry.entry_id,
                "source_subentry_id": subentry.subentry_id,
                "config": _safe_configuration(config_snapshot),
            },
            "memories": await memory.async_backup_data(),
            "temporary_memories": await temporary.async_backup_data(),
            "knowledge": await knowledge.async_backup_data(),
            "archive": await archive.async_backup_data(),
            "usage": await usage.async_backup_data(),
            "guest_mode": await guest_mode.async_backup_data(),
            "request_rules": _safe_configuration(
                await request_rules.async_backup_data()
            ),
        }
        serialized = json.dumps(document, indent=2, ensure_ascii=False)
        if len(serialized.encode("utf-8")) > MAX_BACKUP_BYTES:
            raise BackupError(
                "This agent backup is larger than the supported 16 MB limit"
            )
        safe_title = re.sub(r"[^a-z0-9]+", "-", subentry.title.casefold()).strip("-")
        date = dt_util.utcnow().date().isoformat()
        return {
            "document": document,
            "json": serialized,
            "filename": f"{safe_title or 'conversation-agent'}-full-backup-{date}.json",
        }


def inspect_backup(value: Any, target_agent_id: str) -> PreparedRestore:
    """Parse and validate every category without mutating agent state."""
    if isinstance(value, str):
        if len(value.encode("utf-8")) > MAX_BACKUP_BYTES:
            raise BackupError("The backup file exceeds the 16 MB size limit")
        try:
            value = json.loads(value)
        except json.JSONDecodeError as err:
            raise BackupError("The backup is incomplete or corrupted") from err
    else:
        try:
            encoded_size = len(
                json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode(
                    "utf-8"
                )
            )
        except (TypeError, ValueError) as err:
            raise BackupError("The backup is incomplete or corrupted") from err
        if encoded_size > MAX_BACKUP_BYTES:
            raise BackupError("The backup file exceeds the 16 MB size limit")
    if not isinstance(value, dict):
        raise BackupError("This file is not an Extended OpenAI Conversation backup")
    if value.get("format") != BACKUP_FORMAT:
        raise BackupError("This file is not an Extended OpenAI Conversation backup")
    version = value.get("version")
    if version not in {1, 2, BACKUP_VERSION}:
        if isinstance(version, int) and version > BACKUP_VERSION:
            raise BackupError(
                "This backup was created using a newer unsupported backup format"
            )
        raise BackupError("This backup uses an unsupported backup format version")
    expected = {
        "format",
        "version",
        "created_at",
        "integration_version",
        "agent",
        "memories",
        "temporary_memories",
        "knowledge",
        "archive",
        "usage",
    }
    required = expected | ({"request_rules"} if version >= 3 else set())
    allowed = required | ({"guest_mode"} if version >= 2 else set())
    if not required.issubset(value) or not set(value).issubset(allowed):
        raise BackupError("The backup is incomplete or corrupted")
    created_at = value["created_at"]
    integration_version = value["integration_version"]
    if (
        not isinstance(created_at, str)
        or dt_util.parse_datetime(created_at) is None
        or not isinstance(integration_version, str)
        or not integration_version
    ):
        raise BackupError("The backup metadata is invalid")
    agent = value["agent"]
    if not isinstance(agent, dict) or set(agent) != {
        "title",
        "source_entry_id",
        "source_subentry_id",
        "config",
    }:
        raise BackupError("The agent configuration is incomplete or corrupted")
    title = agent["title"]
    if not isinstance(title, str) or not title.strip() or len(title.strip()) > 255:
        raise BackupError("The backed-up agent name is invalid")
    if not isinstance(agent["source_entry_id"], str) or not isinstance(
        agent["source_subentry_id"], str
    ):
        raise BackupError("The backup agent identity is invalid")
    try:
        raw_config = agent["config"]
        if not isinstance(raw_config, dict):
            raise ValueError("agent config must be an object")
        config = preserve_legacy_guest_policy(
            raw_config, normalize_agent_config(raw_config)
        )
        memories = PersistentMemory.validate_backup_data(value["memories"])
        temporary_memories = TemporaryMemory.validate_backup_data(
            value["temporary_memories"]
        )
        knowledge = KnowledgeLibrary.validate_backup_data(value["knowledge"])
        archive_sessions, archive_turns = ConversationArchive.validate_backup_data(
            value["archive"], target_agent_id
        )
        usage_totals, usage_daily, usage_requests, usage_runs = (
            UsageManager.validate_backup_data(value["usage"], target_agent_id)
        )
        guest_mode_schedule = (
            GuestModeManager.validate_backup_data(value["guest_mode"])
            if version >= 2 and "guest_mode" in value
            else None
        )
        request_rules = RequestRules.validate_backup_data(
            value.get("request_rules", {"defaults": {}, "rules": []})
        )
    except (TypeError, ValueError) as err:
        raise BackupError(f"The backup is incomplete or corrupted: {err}") from err
    return PreparedRestore(
        title.strip(),
        config,
        memories,
        temporary_memories,
        knowledge,
        archive_sessions,
        archive_turns,
        usage_totals,
        usage_daily,
        usage_requests,
        usage_runs,
        guest_mode_schedule,
        request_rules,
        created_at,
        integration_version,
    )


async def async_restore_backup(
    hass: HomeAssistant, entry: Any, subentry: Any, value: Any
) -> dict[str, Any]:
    """Replace all durable categories, rolling back if a commit step fails."""
    prepared = inspect_backup(value, subentry.subentry_id)
    async with _backup_lock(hass, entry.entry_id, subentry.subentry_id):
        managers = await _managers(hass, entry.entry_id, subentry.subentry_id)
        rollback = await _snapshot_for_restore(managers, subentry)
        try:
            await _apply_restore(managers, prepared)
            hass.config_entries.async_update_subentry(
                entry, subentry, data=prepared.config, title=prepared.title
            )
        except Exception as err:
            try:
                await _apply_restore(managers, rollback)
                hass.config_entries.async_update_subentry(
                    entry, subentry, data=rollback.config, title=rollback.title
                )
            except Exception:
                _LOGGER.exception("Agent backup restore rollback failed")
                raise BackupError(
                    "Restore failed and the previous state could not be fully recovered"
                ) from err
            raise BackupError(
                "Restore failed; the previous agent state was recovered"
            ) from err
    return {"status": "restored", "summary": prepared.summary()}


async def _managers(hass: HomeAssistant, entry_id: str, subentry_id: str):
    return (
        await async_get_memory(hass, entry_id, subentry_id),
        await async_get_temporary_memory(hass, entry_id, subentry_id),
        await async_get_knowledge(hass, entry_id, subentry_id),
        await async_get_archive(hass, entry_id, subentry_id),
        await async_get_usage(hass, entry_id, subentry_id),
        await async_get_guest_mode(hass, entry_id, subentry_id),
        await async_get_request_rules(hass, entry_id, subentry_id),
    )


async def _snapshot_for_restore(
    managers: tuple[Any, ...], subentry: Any
) -> PreparedRestore:
    memory, temporary, knowledge, archive, usage, guest_mode, request_rules = managers
    archive_sessions, archive_turns = ConversationArchive.validate_backup_data(
        await archive.async_backup_data(), subentry.subentry_id
    )
    usage_totals, usage_daily, usage_requests, usage_runs = (
        UsageManager.validate_backup_data(
            await usage.async_backup_data(), subentry.subentry_id
        )
    )
    guest_mode_schedule = GuestModeManager.validate_backup_data(
        await guest_mode.async_backup_data()
    )
    request_rule_backup = RequestRules.validate_backup_data(
        await request_rules.async_backup_data()
    )
    return PreparedRestore(
        subentry.title,
        preserve_legacy_guest_policy(
            dict(subentry.data), agent_config_snapshot(subentry.data)
        ),
        PersistentMemory.validate_backup_data(await memory.async_backup_data()),
        TemporaryMemory.validate_backup_data(await temporary.async_backup_data()),
        KnowledgeLibrary.validate_backup_data(await knowledge.async_backup_data()),
        archive_sessions,
        archive_turns,
        usage_totals,
        usage_daily,
        usage_requests,
        usage_runs,
        guest_mode_schedule,
        request_rule_backup,
        dt_util.utcnow().isoformat(),
        _integration_version(),
    )


async def _apply_restore(managers: tuple[Any, ...], prepared: PreparedRestore) -> None:
    memory, temporary, knowledge, archive, usage, guest_mode, request_rules = managers
    usage.request_retention_days = int(
        prepared.config.get(
            CONF_USAGE_REQUEST_RETENTION_DAYS, DEFAULT_USAGE_REQUEST_RETENTION_DAYS
        )
    )
    usage.run_retention_days = int(
        prepared.config.get(
            CONF_USAGE_RUN_RETENTION_DAYS, DEFAULT_USAGE_RUN_RETENTION_DAYS
        )
    )
    await memory.async_replace_backup(prepared.memories)
    await temporary.async_replace_backup(prepared.temporary_memories)
    await knowledge.async_replace_backup(prepared.knowledge)
    await archive.async_replace_backup(
        prepared.archive_sessions, prepared.archive_turns
    )
    await usage.async_replace_backup(
        prepared.usage_totals,
        prepared.usage_daily,
        prepared.usage_requests,
        prepared.usage_runs,
    )
    await guest_mode.async_replace_backup(prepared.guest_mode_schedule)
    await request_rules.async_replace_backup(prepared.request_rules)
