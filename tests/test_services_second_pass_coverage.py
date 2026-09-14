"""Second-pass residual coverage for Home Assistant service handlers."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from homeassistant.exceptions import HomeAssistantError

from custom_components.extended_openai_conversation_responses import services
from custom_components.extended_openai_conversation_responses.const import (
    DOMAIN,
    SERVICE_PROCESS,
)
from tests.test_service_handlers import _Response, _call, _download_handler, _handlers


@pytest.mark.parametrize(
    "helper",
    [
        services.async_set_function_tools_enabled,
        services.async_set_function_groups_enabled,
    ],
)
async def test_function_state_update_handles_entry_disappearing_after_resolution(
    hass, monkeypatch: pytest.MonkeyPatch, helper
) -> None:
    """A config entry removed after target resolution must fail cleanly."""
    monkeypatch.setattr(
        services,
        "resolve_memory_agent",
        lambda *_args: ("entry", "agent"),
    )
    hass.config_entries.async_get_entry.return_value = None

    with pytest.raises(HomeAssistantError, match="Config entry not found"):
        await helper(hass, "entry", "agent", ["target"], True)

    hass.config_entries.async_update_subentry.assert_not_called()


async def test_skill_source_ref_prefers_trimmed_installed_version(
    hass, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Normal installed releases should pin skill downloads to their release version."""
    monkeypatch.setattr(
        services,
        "async_get_integration",
        AsyncMock(return_value=SimpleNamespace(version=" 7.2.1 ")),
    )

    assert await services.async_skill_source_ref(hass) == "7.2.1"


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ([{"name": 123, "type": "file"}], "no valid name"),
        (
            [
                {
                    "name": "SKILL.md",
                    "path": "skills/demo/SKILL.md",
                    "type": "file",
                    "download_url": None,
                    "size": 1,
                }
            ],
            "No download URL",
        ),
        (
            [
                {
                    "name": "assets",
                    "path": "skills/demo/assets",
                    "type": "dir",
                    "url": None,
                }
            ],
            "No API URL",
        ),
    ],
)
async def test_download_skill_rejects_malformed_github_item_metadata(
    hass,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    payload,
    message: str,
) -> None:
    """Malformed GitHub listing metadata must not escape the staging boundary."""
    root_url = (
        "https://api.github.com/repos/conorod1992/extended_openai_conversation/"
        "contents/examples/skills/demo?ref=v1.2.3"
    )
    handler, manager = await _download_handler(
        hass,
        monkeypatch,
        tmp_path,
        {root_url: _Response(200, payload=payload)},
    )

    with pytest.raises(HomeAssistantError, match=message):
        await handler(_call({"skill_name": "demo"}))

    assert not (tmp_path / "staging" / "demo.download-fixed").exists()
    manager.async_publish_staged_skill.assert_not_awaited()


async def test_download_skill_rejects_failed_file_download_and_cleans_staging(
    hass, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """A listed file that later fails to download must leave no staged install."""
    root_url = (
        "https://api.github.com/repos/conorod1992/extended_openai_conversation/"
        "contents/examples/skills/demo?ref=v1.2.3"
    )
    responses = {
        root_url: _Response(
            200,
            payload=[
                {
                    "name": "SKILL.md",
                    "path": "skills/demo/SKILL.md",
                    "type": "file",
                    "download_url": "download:skill",
                    "size": 7,
                }
            ],
        ),
        "download:skill": _Response(503),
    }
    handler, manager = await _download_handler(
        hass, monkeypatch, tmp_path, responses
    )

    with pytest.raises(HomeAssistantError, match="Failed to download"):
        await handler(_call({"skill_name": "demo"}))

    assert not (tmp_path / "staging" / "demo.download-fixed").exists()
    manager.async_publish_staged_skill.assert_not_awaited()


async def test_process_auto_selection_skips_stale_and_unrelated_registry_entries(
    hass, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Automatic agent discovery should ignore stale/non-integration registry rows."""
    agent = SimpleNamespace(
        entity_id="conversation.good",
        async_process_direct=AsyncMock(
            return_value=(
                SimpleNamespace(
                    response=SimpleNamespace(
                        speech={"plain": {"speech": "Hello from the valid agent"}}
                    ),
                    conversation_id="conversation-1",
                ),
                {"handled_locally": False},
            )
        ),
    )
    registry = SimpleNamespace(
        entities={
            "wrong-platform": SimpleNamespace(
                platform="other",
                domain="conversation",
                entity_id="conversation.other",
            ),
            "wrong-domain": SimpleNamespace(
                platform=DOMAIN,
                domain="sensor",
                entity_id="sensor.not_an_agent",
            ),
            "stale": SimpleNamespace(
                platform=DOMAIN,
                domain="conversation",
                entity_id="conversation.stale",
            ),
            "good": SimpleNamespace(
                platform=DOMAIN,
                domain="conversation",
                entity_id="conversation.good",
            ),
        }
    )
    monkeypatch.setattr(services.er, "async_get", lambda _hass: registry)
    monkeypatch.setattr(
        services.conversation,
        "async_get_agent",
        lambda _hass, entity_id: agent if entity_id == "conversation.good" else None,
    )
    handlers = await _handlers(hass)

    result = await handlers[SERVICE_PROCESS](_call({"text": "Hello"}))

    assert result == {
        "response": "Hello from the valid agent",
        "conversation_id": "conversation-1",
        "handled_locally": False,
    }
    agent.async_process_direct.assert_awaited_once()
