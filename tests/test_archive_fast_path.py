"""Tests for archive-disabled request-path behavior."""

from custom_components.extended_openai_conversation_responses.conversation_archive import (
    ConversationArchive,
)
from custom_components.extended_openai_conversation_responses.lifecycle_optimizations import (
    install_lifecycle_optimizations,
)
from custom_components.extended_openai_conversation_responses.scope import user_scope


async def test_archive_disabled_does_not_create_or_persist_session() -> None:
    """A disabled archive returns before touching archive state or storage."""
    install_lifecycle_optimizations()
    archive = ConversationArchive.__new__(ConversationArchive)

    result = await archive.async_begin_session(
        "conversation:test",
        user_scope("user", source="test"),
        "conversation-id",
        archive_enabled=False,
        shared_archive_enabled=False,
        inactivity_minutes=30,
    )

    assert result is None
