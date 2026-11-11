"""On-disk snapshots of historical removal transactions, without the retired API."""

from uuid import uuid4

from custom_components.extended_openai_conversation_responses.skill_transactions import (
    commit_transaction,
    prepare_transaction,
)


def stage_legacy_removal(manager, name, *, committed=False):
    """Leave the exact journal/rename cut point that older installations wrote."""
    target = manager.user_skills_dir / name
    if not target.exists():
        return False
    backup = manager.staging_dir / f"{name}.remove-{uuid4().hex}"
    journal = prepare_transaction(manager.staging_dir, target, backup, None)
    target.rename(backup)
    if committed:
        commit_transaction(journal)
    return True
