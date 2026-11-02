"""Content-free observations of retained storage, without invoking Store loaders."""

from __future__ import annotations

import os
from pathlib import Path
import re
from typing import Any

from .storage_namespaces import (
    ARCHIVE_PREFIX,
    GUEST_MODE_PREFIX,
    KNOWLEDGE_PREFIX,
    MEMORY_PREFIX,
    REQUEST_RULES_PREFIX,
    RESTORE_JOURNAL_PREFIX,
    TEMPORARY_MEMORY_PREFIX,
    USAGE_PREFIX,
)


def storage_recovery_snapshot(
    directory: str, entry_id: str, subentry_id: str
) -> dict[str, Any]:
    """Inspect names/types only; neither read, initialize nor repair any store.

    A retained corruption sidecar is not itself proof of a current failure: a
    validated recovery may already have replaced its active file. Conversely,
    an existing active file is not evidence that its contents are valid.
    """
    identity = f"{entry_id}.{subentry_id}"
    exact = {
        f"{prefix}.{identity}{suffix}": category
        for prefix, suffix, category in (
            (MEMORY_PREFIX, "", "persistent_memory"),
            (TEMPORARY_MEMORY_PREFIX, "", "temporary_memory"),
            (KNOWLEDGE_PREFIX, "", "knowledge"),
            (GUEST_MODE_PREFIX, "", "guest_mode"),
            (REQUEST_RULES_PREFIX, "", "request_rules"),
            (ARCHIVE_PREFIX, ".metadata", "archive"),
            (USAGE_PREFIX, "", "usage"),
            (USAGE_PREFIX, ".daily", "usage"),
            (USAGE_PREFIX, ".details", "usage"),
            (RESTORE_JOURNAL_PREFIX, "", "restore_journal"),
        )
    }
    archive_partition = re.compile(
        re.escape(f"{ARCHIVE_PREFIX}.{identity}.turns.") + r"[0-9]{4}-(?:0[1-9]|1[0-2])"
    )
    stores = {
        category: {
            "active_files": 0,
            "preserved_corrupt_files": 0,
            "unavailable_stores": 0,
        }
        for category in sorted(set(exact.values()))
    }
    active: set[str] = set()
    unavailable: dict[str, str] = {}
    evidence: dict[str, str] = {}
    try:
        entries = os.scandir(Path(directory))
    except FileNotFoundError:
        entries = None
    if entries is not None:
        with entries:
            for item in entries:
                base, marker, _ = item.name.partition(".corrupt.")
                category = exact.get(base)
                if category is None and archive_partition.fullmatch(base):
                    category = "archive"
                if category is None:
                    continue
                if marker:
                    # Do not follow symlinks or open even a preserved file.
                    evidence[base] = category
                    if item.is_file(follow_symlinks=False):
                        stores[category]["preserved_corrupt_files"] += 1
                else:
                    # HA can read an administrator's active-file symlink. A
                    # metadata stat preserves that compatibility without opening
                    # its target or claiming that its contents are valid.
                    if item.is_file():
                        stores[category]["active_files"] += 1
                        active.add(base)
                    else:
                        unavailable[base] = category
    unavailable.update(
        (base, category) for base, category in evidence.items() if base not in active
    )
    for category in unavailable.values():
        stores[category]["unavailable_stores"] += 1
    journal = stores.pop("restore_journal")
    journal_state = (
        "unavailable"
        if journal["unavailable_stores"]
        else "present"
        if journal["active_files"]
        else "absent"
    )
    report: dict[str, Any] = {
        "content_validated": False,
        "stores": stores,
        "restore_journal": {**journal, "state": journal_state},
    }
    if unavailable:
        report["guidance"] = (
            "Preserve the original files and backups. Resolve any pending restore "
            "recovery first; without a usable journal, recover a validated backup "
            "into a clean assistant or seek administrator repair with Home Assistant stopped."
        )
    return report
