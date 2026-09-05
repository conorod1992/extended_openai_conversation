"""Regression tests for runtime configuration state enforcement."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_API_KEY
from homeassistant.exceptions import HomeAssistantError

from custom_components.extended_openai_conversation_responses.const import (
    CONF_API_PROVIDER,
    CONF_BASE_URL,
    CONF_ORGANIZATION,
    CONF_SKIP_AUTHENTICATION,
)
from custom_components.extended_openai_conversation_responses.conversation_archive import (
    ConversationArchive,
)
from custom_components.extended_openai_conversation_responses.provider_credentials import (
    async_replace_api_key,
)
from custom_components.extended_openai_conversation_responses.scope import shared_scope


class FakeArchiveStorage:
    def __init__(self) -> None:
        self.metadata = None
        self.partitions = {}

    async def async_load_metadata(self):
        return deepcopy(self.metadata)

    async def async_save_metadata(self, data):
        self.metadata = deepcopy(data)

    async def async_load_partition(self, partition):
        return deepcopy(self.partitions.get(partition))

    async def async_save_partition(self, partition, data):
        self.partitions[partition] = deepcopy(data)


async def _archive() -> ConversationArchive:
    archive = ConversationArchive(FakeArchiveStorage(), "agent-1")
    await archive.async_initialize()
    return archive


async def test_api_key_rotation_rejects_provider_change_during_validation() -> None:
    entry = SimpleNamespace(
        entry_id="entry-1",
        data={
            CONF_API_KEY: "old-key",
            CONF_API_PROVIDER: "openai",
            CONF_BASE_URL: "https://old.example/v1",
            CONF_ORGANIZATION: "org-1",
            CONF_SKIP_AUTHENTICATION: False,
        },
        state=ConfigEntryState.LOADED,
        disabled_by=None,
    )
    hass = MagicMock()
    hass.config_entries.async_update_entry = MagicMock(return_value=True)

    async def validate(**_kwargs):
        entry.data = {**entry.data, CONF_BASE_URL: "https://new.example/v1"}
        return object()

    with (
        patch(
            "custom_components.extended_openai_conversation_responses.provider_credentials.get_authenticated_client",
            AsyncMock(side_effect=validate),
        ),
        pytest.raises(HomeAssistantError, match="Provider settings changed"),
    ):
        await async_replace_api_key(hass, entry, "new-key")

    assert entry.data[CONF_BASE_URL] == "https://new.example/v1"
    assert entry.data[CONF_API_KEY] == "old-key"
    hass.config_entries.async_update_entry.assert_not_called()


async def test_disabling_shared_archive_stops_reusing_retained_session() -> None:
    archive = await _archive()
    scope = shared_scope(source="shared_voice_policy")
    retained = await archive.async_begin_session(
        "shared",
        scope,
        "conversation-1",
        archive_enabled=True,
        shared_archive_enabled=True,
        inactivity_minutes=30,
    )
    await archive.async_record_turn(
        retained.session_id,
        run_id="run-1",
        user_text="keep this",
        assistant_text="kept",
        successful=True,
    )

    unretained = await archive.async_begin_session(
        "shared",
        scope,
        "conversation-1",
        archive_enabled=True,
        shared_archive_enabled=False,
        inactivity_minutes=30,
    )

    assert unretained.session_id != retained.session_id
    assert unretained.retention_state == "unretained"
    assert (
        await archive.async_record_turn(
            unretained.session_id,
            run_id="run-2",
            user_text="do not keep",
            assistant_text="not kept",
            successful=True,
        )
        is None
    )
    assert archive.stats()["turn_count"] == 1


async def test_resume_saving_respects_disabled_shared_archive() -> None:
    archive = await _archive()
    scope = shared_scope(source="shared_voice_policy")
    session = await archive.async_begin_session(
        "shared",
        scope,
        "conversation-1",
        archive_enabled=True,
        shared_archive_enabled=True,
        inactivity_minutes=30,
    )
    await archive.async_make_private(session.session_id)

    resumed = await archive.async_resume_saving(
        "shared",
        session.session_id,
        scope,
        shared_archive_enabled=False,
    )

    assert resumed.retention_state == "unretained"
    assert (
        await archive.async_record_turn(
            resumed.session_id,
            run_id="run-1",
            user_text="do not retain",
            assistant_text="not retained",
            successful=True,
        )
        is None
    )
