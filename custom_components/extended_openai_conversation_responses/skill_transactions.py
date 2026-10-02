"""Durable intent for same-filesystem Skill publication and removal."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import re
import shutil
import tempfile

from homeassistant.exceptions import HomeAssistantError

_LOGGER = logging.getLogger(__name__)
_TRANSACTION = re.compile(r"transaction-([0-9a-f]{32})\.json")


def _identity(path: Path) -> list[int] | None:
    if path.is_symlink():
        raise HomeAssistantError("Skill transaction paths must not be symbolic links")
    try:
        state = path.stat()
    except FileNotFoundError:
        return None
    return [state.st_dev, state.st_ino]


def _remove(path: Path) -> None:
    if path.is_symlink():
        raise HomeAssistantError("Skill recovery refuses a symbolic link")
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def _write(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".transaction-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(document, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def prepare_transaction(
    root: Path, target: Path, backup: Path, staged: Path | None
) -> Path:
    """Record exact owned paths and identities before the first destructive rename."""
    token = backup.name.rsplit("-", 1)[1]
    journal = root / f"transaction-{token}.json"
    document = {
        "version": 1,
        "phase": "prepared",
        "name": target.name,
        "kind": "publish" if staged is not None else "remove",
        "backup": backup.name,
        "previous_identity": _identity(target),
        "staged": str(staged.relative_to(root.resolve()))
        if staged is not None
        else None,
        "candidate_identity": _identity(staged) if staged is not None else None,
    }
    _write(journal, document)
    return journal


def commit_transaction(journal: Path) -> None:
    document = json.loads(journal.read_text())
    _write(journal, {**document, "phase": "committed"})


def recover_transaction(journal: Path, installed: Path) -> None:
    """Roll back uncommitted renames; retire committed backups without republishing."""
    if journal.is_symlink() or journal.stat().st_size > 16 * 1024:
        raise HomeAssistantError("Skill recovery record is unsafe or oversized")
    match = _TRANSACTION.fullmatch(journal.name)
    document = json.loads(journal.read_text())
    root = journal.parent.resolve()
    name, kind = document.get("name"), document.get("kind")
    if (
        match is None
        or document.get("version") != 1
        or document.get("phase") not in {"prepared", "committed"}
        or not isinstance(name, str)
        or not name
        or name in {".", ".."}
        or Path(name).name != name
        or kind not in {"publish", "remove"}
    ):
        raise HomeAssistantError(f"Invalid Skill recovery record: {journal.name}")
    expected_backup = f"{name}.{'backup' if kind == 'publish' else 'remove'}-{match[1]}"
    if document.get("backup") != expected_backup:
        raise HomeAssistantError("Skill recovery backup ownership does not match")
    target, backup = installed / name, root / expected_backup
    staged = None
    if kind == "publish":
        if not isinstance(document.get("staged"), str):
            raise HomeAssistantError("Skill recovery candidate path is missing")
        staged = (root / document["staged"]).resolve()
        if staged == root or not staged.is_relative_to(root):
            raise HomeAssistantError("Skill recovery candidate escaped staging")
    previous, candidate = (
        document.get("previous_identity"),
        document.get("candidate_identity"),
    )
    for identity in (previous, candidate):
        if identity is not None and (
            not isinstance(identity, list)
            or len(identity) != 2
            or any(type(v) is not int for v in identity)
        ):
            raise HomeAssistantError("Skill recovery identity is invalid")
    if kind == "publish" and candidate is None:
        raise HomeAssistantError("Skill recovery candidate ownership is missing")
    backup_identity = _identity(backup)
    if backup_identity is not None and backup_identity != previous:
        raise HomeAssistantError(
            "Skill recovery backup identity changed; manual recovery required"
        )
    if document["phase"] == "prepared":
        current = _identity(target)
        if backup_identity is not None:
            if current is not None:
                if kind != "publish" or current != candidate:
                    raise HomeAssistantError(
                        "Skill recovery target changed; manual recovery required"
                    )
                _remove(target)
            backup.rename(target)
        elif previous is None:
            if current is not None:
                if current != candidate:
                    raise HomeAssistantError(
                        "Skill recovery target is not the interrupted candidate"
                    )
                _remove(target)
        elif current != previous:
            raise HomeAssistantError(
                "Known-good Skill is unavailable; manual recovery required"
            )
    if backup.exists():
        _remove(backup)
    if staged is not None and _identity(staged) == candidate:
        _remove(staged)
    journal.unlink()


def recover_transactions(root: Path, installed: Path) -> None:
    """Recover only explicit owned transactions, preserving unjournaled residue."""
    if not root.exists():
        return
    if root.is_symlink():
        raise HomeAssistantError("Skill staging root must not be a symbolic link")
    for journal in sorted(root.glob("transaction-*.json")):
        if journal.is_symlink():
            raise HomeAssistantError(
                "Skill recovery journal must not be a symbolic link"
            )
        recover_transaction(journal, installed)
    for pattern in ("*.backup-*", "*.remove-*"):
        for residue in root.glob(pattern):
            _LOGGER.warning(
                "Unjournaled Skill recovery data remains at %s; inspect and recover manually before deleting it",
                residue,
            )
