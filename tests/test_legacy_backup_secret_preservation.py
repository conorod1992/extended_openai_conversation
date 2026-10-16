"""Legacy Management restores obey destination-aware credential preservation."""

from copy import deepcopy

import pytest

from custom_components.extended_openai_conversation_responses import (
    backup,
    management_ui,
)
from custom_components.extended_openai_conversation_responses.agent_config import (
    agent_config_snapshot,
)
from custom_components.extended_openai_conversation_responses.const import DOMAIN
from tests.test_backup import _document
from tests.test_secret_restore_context import _rest_tool
from tests.test_selective_restore_concurrency import RestoreHarness


@pytest.mark.parametrize("context", ["unchanged", "changed", "missing"])
async def test_legacy_management_restore_preserves_or_rejects_redacted_credentials(
    monkeypatch, hass, context
):
    tx = RestoreHarness(monkeypatch)
    tx.subentry.data["functions"] = [_rest_tool()]
    tx.subentry.data["function_groups"] = []
    tx.entry.domain = DOMAIN
    tx.entry.subentries = {tx.subentry.subentry_id: tx.subentry}
    tx.subentry.subentry_type = "conversation"
    tx.hass.config_entries.async_get_entry = lambda _entry_id: tx.entry
    document = _document()
    document["agent"]["config"] = deepcopy(tx.subentry.data)
    document["agent"]["title"] = tx.subentry.title
    document = backup.finalize_backup_snapshot(document)["document"]
    if context == "changed":
        document["agent"]["config"]["functions"][0]["function"]["resource"] = (
            "https://other.example.test/api"
        )
    elif context == "missing":
        tx.subentry.data["functions"][0]["function"]["headers"] = {}
    before = deepcopy(tx.subentry.data)

    async def restore():
        return await management_ui.async_management_command(
            tx.hass,
            "admin",
            True,
            {
                "section": "backup",
                "action": "restore",
                "entry_id": tx.entry.entry_id,
                "subentry_id": tx.subentry.subentry_id,
                "document": document,
                "confirm": True,
            },
        )

    if context != "unchanged":
        with pytest.raises(
            backup.BackupError, match="Cannot safely restore unavailable secrets"
        ):
            await restore()
        assert tx.subentry.data == before
        assert not tx.writes
        assert not tx.journal.saved
    else:
        result = await restore()
        assert result["status"] == "restored"
        restored = agent_config_snapshot(tx.subentry.data)["functions"]
        assert (
            restored[0]["function"]["headers"]["Authorization"]
            == "Bearer local-credential"
        )
        assert [saved["phase"] for saved in tx.journal.saved] == [
            "applying",
            "committed",
        ]
        assert tx.journal.data is None
    assert not tx.gate._writer_active
