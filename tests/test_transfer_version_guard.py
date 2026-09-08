"""Compatibility guard for future unified transfer documents."""

import pytest

from custom_components.extended_openai_conversation_responses import backup, transfer


def test_future_transfer_version_is_rejected_cleanly() -> None:
    document = {
        "format": transfer.TRANSFER_FORMAT,
        "version": transfer.TRANSFER_VERSION + 1,
        "mode": "setup",
        "created_at": "2026-09-08T20:00:00+00:00",
        "integration_version": "99.0.0",
        "agent": {
            "title": "Future agent",
            "source_entry_id": "entry",
            "source_subentry_id": "agent",
        },
        "sections": {
            transfer.SECTION_CONFIGURATION: {},
            transfer.SECTION_REQUEST_RULES: {},
        },
    }

    with pytest.raises(backup.BackupError, match="newer unsupported format"):
        transfer.inspect_transfer(document, "target-agent")
