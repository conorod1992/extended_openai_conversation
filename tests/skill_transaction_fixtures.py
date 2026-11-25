"""On-disk snapshots written by older Skill installation and removal actions."""

import json
from pathlib import Path
from uuid import uuid4


def write_legacy_transaction(
    root: Path, target: Path, backup: Path, staged: Path | None
) -> Path:
    def identity(path):
        if path is None or not path.exists():
            return None
        info = path.stat()
        return [info.st_dev, info.st_ino]

    root.mkdir(parents=True, exist_ok=True)
    token = backup.name.rsplit("-", 1)[1]
    journal = root / f"transaction-{token}.json"
    journal.write_text(
        json.dumps(
            {
                "version": 1,
                "phase": "prepared",
                "name": target.name,
                "kind": "publish" if staged is not None else "remove",
                "backup": backup.name,
                "previous_identity": identity(target),
                "staged": str(staged.relative_to(root)) if staged is not None else None,
                "candidate_identity": identity(staged),
            }
        )
    )
    return journal


def commit_legacy_transaction(journal: Path) -> None:
    document = json.loads(journal.read_text())
    document["phase"] = "committed"
    journal.write_text(json.dumps(document))


def stage_legacy_rename(manager, name, staged=None, *, committed=False):
    """Leave a historical transaction after its first rename."""
    root = manager.staging_dir
    target = manager.user_skills_dir / name
    suffix = "backup" if staged is not None else "remove"
    backup = root / f"{name}.{suffix}-{uuid4().hex}"
    journal = write_legacy_transaction(root, target, backup, staged)
    target.rename(backup)
    if committed:
        commit_legacy_transaction(journal)


def stage_legacy_removal(manager, name, *, committed=False):
    if not (manager.user_skills_dir / name).exists():
        return False
    stage_legacy_rename(manager, name, committed=committed)
    return True
