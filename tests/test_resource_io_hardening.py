"""Regression tests for resource bounds and filesystem I/O hardening."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import voluptuous as vol
import yaml

from homeassistant.components import conversation
from homeassistant.exceptions import HomeAssistantError

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_RESPONSES,
)
from custom_components.extended_openai_conversation_responses.entity import (
    ExtendedOpenAIBaseLLMEntity,
    _convert_content_to_responses_param,
)
from custom_components.extended_openai_conversation_responses.functions import (
    NativeFunction,
    SqliteFunction,
)
import custom_components.extended_openai_conversation_responses.functions.native as native_module
from custom_components.extended_openai_conversation_responses.resource_limits import (
    MAX_ATTACHMENT_COUNT,
    MAX_LOCAL_ATTACHMENT_BYTES,
)
from custom_components.extended_openai_conversation_responses.services import (
    QUERY_IMAGE_SCHEMA,
    prepare_image_params,
)


def _make_sparse_file(path: Path, size: int) -> None:
    """Create a file with the requested logical size without allocating its contents."""
    with path.open("wb") as handle:
        handle.truncate(size)


async def test_sqlite_single_empty_and_row_limit_are_safe(
    hass, tmp_path: Path
) -> None:
    """SQLite empty single results and oversized result sets fail cleanly."""
    import sqlite3

    db_path = tmp_path / "bounded.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE sample (value INTEGER)")
        conn.executemany("INSERT INTO sample VALUES (?)", [(1,), (2,), (3,)])

    function = SqliteFunction()
    base_config = {"type": "sqlite", "db_url": f"file:{db_path}"}

    empty = await function.execute(
        hass,
        {**base_config, "query": "SELECT value FROM sample WHERE value = 99", "single": True},
        {},
        None,
        [],
    )
    assert empty == {}

    with pytest.raises(HomeAssistantError, match="more than 2 rows"):
        await function.execute(
            hass,
            {**base_config, "query": "SELECT value FROM sample", "max_rows": 2},
            {},
            None,
            [],
        )

    with pytest.raises(HomeAssistantError, match="did not return any columns"):
        await function.execute(
            hass,
            {**base_config, "query": "BEGIN"},
            {},
            None,
            [],
        )


async def test_chat_attachment_rejects_oversized_local_file(
    hass, tmp_path: Path
) -> None:
    """Chat attachments are rejected before reading an oversized local file."""
    image_path = tmp_path / "large.png"
    _make_sparse_file(image_path, MAX_LOCAL_ATTACHMENT_BYTES + 1)
    chat_log = conversation.ChatLog(hass, "conversation-id")
    chat_log.async_add_user_content(
        conversation.UserContent(
            content="inspect this",
            attachments=[
                conversation.Attachment(
                    media_content_id="media-source://camera/large",
                    mime_type="image/png",
                    path=image_path,
                )
            ],
        )
    )
    messages = _convert_content_to_responses_param(chat_log.content)
    entity = ExtendedOpenAIBaseLLMEntity.__new__(ExtendedOpenAIBaseLLMEntity)
    entity.hass = hass

    with pytest.raises(HomeAssistantError, match="too large"):
        await entity._async_add_attachments(chat_log, messages, API_MODE_RESPONSES)


async def test_chat_attachment_count_is_bounded(hass, tmp_path: Path) -> None:
    """An excessive number of chat attachments is rejected before encoding."""
    image_path = tmp_path / "small.png"
    image_path.write_bytes(b"image")
    attachment = conversation.Attachment(
        media_content_id="media-source://camera/test",
        mime_type="image/png",
        path=image_path,
    )
    chat_log = conversation.ChatLog(hass, "conversation-id")
    chat_log.async_add_user_content(
        conversation.UserContent(
            content="inspect these",
            attachments=[attachment] * (MAX_ATTACHMENT_COUNT + 1),
        )
    )
    messages = _convert_content_to_responses_param(chat_log.content)
    entity = ExtendedOpenAIBaseLLMEntity.__new__(ExtendedOpenAIBaseLLMEntity)
    entity.hass = hass

    with pytest.raises(HomeAssistantError, match="At most"):
        await entity._async_add_attachments(chat_log, messages, API_MODE_RESPONSES)


def test_query_image_schema_and_local_file_are_bounded(hass, tmp_path: Path) -> None:
    """Image-query count and local-file size are bounded before base64 allocation."""
    with pytest.raises(vol.Invalid):
        QUERY_IMAGE_SCHEMA(
            {
                "config_entry": "entry",
                "prompt": "inspect",
                "images": [
                    {"url": f"https://example.test/{index}.png"}
                    for index in range(MAX_ATTACHMENT_COUNT + 1)
                ],
            }
        )

    image_path = tmp_path / "large.png"
    _make_sparse_file(image_path, MAX_LOCAL_ATTACHMENT_BYTES + 1)
    hass.config.is_allowed_path = MagicMock(return_value=True)
    with pytest.raises(HomeAssistantError, match="too large"):
        prepare_image_params(hass, [{"url": str(image_path)}])


async def test_add_automation_owns_id_and_preserves_existing_yaml(
    hass, tmp_path: Path, monkeypatch
) -> None:
    """Automation creation replaces caller IDs and atomically preserves prior items."""
    automation_path = tmp_path / "automations.yaml"
    original = "- id: existing\n  alias: Existing\n  trigger: []\n  action: []\n"
    automation_path.write_text(original, encoding="utf-8")
    monkeypatch.setattr(native_module, "AUTOMATION_CONFIG_PATH", str(automation_path))
    monkeypatch.setattr(
        native_module.automation.config,
        "_async_validate_config_item",
        AsyncMock(return_value=None),
    )
    hass.services.async_call = AsyncMock(return_value=None)

    function = NativeFunction()
    result = await function.add_automation(
        hass,
        {"type": "native", "name": "add_automation"},
        {
            "automation_config": (
                "id: caller-controlled\n"
                "alias: New automation\n"
                "trigger: []\n"
                "action: []\n"
            )
        },
        None,
        [],
    )

    assert result == "Success"
    saved = yaml.safe_load(automation_path.read_text(encoding="utf-8"))
    assert [item["alias"] for item in saved] == ["Existing", "New automation"]
    assert saved[1]["id"] != "caller-controlled"
    assert saved[1]["id"] != saved[0]["id"]
    hass.services.async_call.assert_awaited_once_with(
        native_module.automation.config.DOMAIN,
        native_module.SERVICE_RELOAD,
        blocking=True,
    )


async def test_add_automation_rejects_multiple_definitions(
    hass, tmp_path: Path, monkeypatch
) -> None:
    """The singular add_automation tool must not silently discard list entries."""
    automation_path = tmp_path / "automations.yaml"
    automation_path.write_text("", encoding="utf-8")
    monkeypatch.setattr(native_module, "AUTOMATION_CONFIG_PATH", str(automation_path))
    function = NativeFunction()

    with pytest.raises(HomeAssistantError, match="exactly one automation"):
        await function.add_automation(
            hass,
            {"type": "native", "name": "add_automation"},
            {"automation_config": "- alias: One\n- alias: Two\n"},
            None,
            [],
        )


async def test_add_automation_rolls_back_when_reload_fails(
    hass, tmp_path: Path, monkeypatch
) -> None:
    """A failed automation reload restores the exact previous file contents."""
    automation_path = tmp_path / "automations.yaml"
    original = "- id: existing\n  alias: Existing\n  trigger: []\n  action: []\n"
    automation_path.write_text(original, encoding="utf-8")
    monkeypatch.setattr(native_module, "AUTOMATION_CONFIG_PATH", str(automation_path))
    monkeypatch.setattr(
        native_module.automation.config,
        "_async_validate_config_item",
        AsyncMock(return_value=None),
    )
    hass.services.async_call = AsyncMock(
        side_effect=[HomeAssistantError("reload failed"), None]
    )
    function = NativeFunction()

    with pytest.raises(HomeAssistantError, match="reload failed"):
        await function.add_automation(
            hass,
            {"type": "native", "name": "add_automation"},
            {
                "automation_config": (
                    "alias: New automation\ntrigger: []\naction: []\n"
                )
            },
            None,
            [],
        )

    assert automation_path.read_text(encoding="utf-8") == original
    assert hass.services.async_call.await_count == 2


async def test_concurrent_add_automation_calls_do_not_lose_updates(
    hass, tmp_path: Path, monkeypatch
) -> None:
    """Concurrent tool calls serialize their read-modify-write cycle."""
    automation_path = tmp_path / "automations.yaml"
    automation_path.write_text(
        "- id: existing\n  alias: Existing\n  trigger: []\n  action: []\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(native_module, "AUTOMATION_CONFIG_PATH", str(automation_path))
    monkeypatch.setattr(
        native_module.automation.config,
        "_async_validate_config_item",
        AsyncMock(return_value=None),
    )
    hass.services.async_call = AsyncMock(return_value=None)
    function = NativeFunction()

    async def add(alias: str) -> None:
        await function.add_automation(
            hass,
            {"type": "native", "name": "add_automation"},
            {"automation_config": f"alias: {alias}\ntrigger: []\naction: []\n"},
            None,
            [],
        )

    await asyncio.gather(add("First"), add("Second"))

    saved = yaml.safe_load(automation_path.read_text(encoding="utf-8"))
    assert {item["alias"] for item in saved} == {"Existing", "First", "Second"}
    assert len({item["id"] for item in saved}) == 3
