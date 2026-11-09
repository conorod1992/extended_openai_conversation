"""Optional Knowledge recovery must not prevent inspecting the fresh request."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.extended_openai_conversation_responses import (
    management_request_preview as preview,
)
from custom_components.extended_openai_conversation_responses.agent_config import (
    agent_config_snapshot,
)
from custom_components.extended_openai_conversation_responses.guest_mode import (
    GuestCapabilityPolicy,
)
from custom_components.extended_openai_conversation_responses.knowledge import (
    KnowledgeLibrary,
)
from tests.test_function_groups import _tool
from tests.test_knowledge import FakeStorage


async def test_preview_survives_cached_initialization_failure_and_recovers(
    hass, monkeypatch
):
    storage = FakeStorage({"sources": 42})
    library = KnowledgeLibrary(storage)
    with pytest.raises(ValueError, match="sources"):
        await library.async_initialize()
    assert not library.initialized
    options = agent_config_snapshot(
        {
            "chat_model": "gpt-4.1",
            "api_mode": "responses",
            "functions": [_tool("preview_probe")],
            "function_groups": [],
            "knowledge_enabled": True,
            "memory_mode": "off",
            "temporary_memory": "off",
        }
    )
    monkeypatch.setattr(
        preview,
        "async_get_guest_mode",
        AsyncMock(
            return_value=SimpleNamespace(status=lambda: {"currently_active": False})
        ),
    )
    monkeypatch.setattr(
        preview, "resolve_guest_policy", lambda *_: GuestCapabilityPolicy.unrestricted()
    )
    monkeypatch.setattr(preview, "get_loaded_knowledge", lambda *_: library)
    monkeypatch.setattr(preview, "get_loaded_temporary_memory", lambda *_: None)
    monkeypatch.setattr(preview.SkillManager, "get_loaded_instance", lambda: None)
    monkeypatch.setattr(preview, "get_exposed_entities", lambda *_: [])
    render = Mock(return_value=SimpleNamespace(text="Instructions", sections=[]))
    monkeypatch.setattr(preview, "render_effective_prompt", render)

    async def inspect():
        return await preview.async_preview_effective_request(
            hass,
            SimpleNamespace(entry_id="entry", data={}),
            SimpleNamespace(subentry_id="assistant"),
            options,
            "admin",
        )

    unavailable = await inspect()
    assert render.call_args.kwargs["knowledge_available"] is False
    assert any(
        "Knowledge Library is unavailable" in note for note in unavailable["notes"]
    )
    assert storage.data == {"sources": 42}  # Inspection must not repair or replace it.

    storage.data = {"sources": []}
    await library.async_initialize()
    await library.async_create("Reference", "Useful information", "The answer is here.")
    recovered = await inspect()
    assert render.call_args.kwargs["knowledge_available"] is True
    assert not any(
        "Knowledge Library is unavailable" in note for note in recovered["notes"]
    )
