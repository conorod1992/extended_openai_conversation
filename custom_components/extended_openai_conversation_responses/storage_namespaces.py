"""Durable per-agent storage identities, independent of runtime registries.

Values are compatibility-sensitive filenames; partition suffixes remain owned
by their stores. This list describes cleanup ownership, not backup categories.
"""

from .const import DOMAIN

MEMORY_PREFIX = f"{DOMAIN}.memory"
TEMPORARY_MEMORY_PREFIX = f"{DOMAIN}.temporary_memory"
KNOWLEDGE_PREFIX = f"{DOMAIN}.knowledge"
ARCHIVE_PREFIX = f"{DOMAIN}.archive"
USAGE_PREFIX = f"{DOMAIN}.usage"
GUEST_MODE_PREFIX = f"{DOMAIN}.guest_mode"
REQUEST_RULES_PREFIX = f"{DOMAIN}.request_rules"
RESTORE_JOURNAL_PREFIX = f"{DOMAIN}.restore_transaction"

AGENT_STORAGE_PREFIXES = (
    MEMORY_PREFIX,
    TEMPORARY_MEMORY_PREFIX,
    KNOWLEDGE_PREFIX,
    ARCHIVE_PREFIX,
    USAGE_PREFIX,
    GUEST_MODE_PREFIX,
    REQUEST_RULES_PREFIX,
    RESTORE_JOURNAL_PREFIX,
)
