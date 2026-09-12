"""Focused coverage for entity attachment preparation and message injection."""

from types import SimpleNamespace

import pytest

from homeassistant.exceptions import HomeAssistantError

from custom_components.extended_openai_conversation_responses import entity


class _FakeHass:
    """Run executor work inline while retaining the async call contract."""

    def __init__(self) -> None:
        self.executor_calls = 0

    async def async_add_executor_job(self, target, *args):
        self.executor_calls += 1
        return target(*args)


class _FakeUserContent:
    role = "user"

    def __init__(self, *, attachments: list[object] | None = None) -> None:
        self.attachments = attachments or []


async def _add_attachments(
    monkeypatch: pytest.MonkeyPatch,
    chat_content: list[object],
    messages: list[dict[str, object]],
    api_mode: str,
) -> _FakeHass:
    monkeypatch.setattr(entity.conversation, "UserContent", _FakeUserContent)
    hass = _FakeHass()
    self_like = SimpleNamespace(hass=hass)
    await entity.ExtendedOpenAIBaseLLMEntity._async_add_attachments(
        self_like,
        SimpleNamespace(content=chat_content),
        messages,
        api_mode,
    )
    return hass


@pytest.mark.asyncio
async def test_async_add_attachments_returns_without_user_or_attachments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Attachment work is skipped when the active turn has nothing to prepare."""
    messages = [{"role": "user", "content": "hello"}]

    hass = await _add_attachments(monkeypatch, [object()], messages, "chat_completions")
    assert hass.executor_calls == 0
    assert messages == [{"role": "user", "content": "hello"}]

    hass = await _add_attachments(
        monkeypatch,
        [_FakeUserContent()],
        messages,
        "chat_completions",
    )
    assert hass.executor_calls == 0
    assert messages == [{"role": "user", "content": "hello"}]


@pytest.mark.asyncio
async def test_async_add_attachments_enforces_count_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Too many attachments fail before any filesystem work is dispatched."""
    user = _FakeUserContent(
        attachments=[SimpleNamespace(path="unused")]
        * (entity.MAX_ATTACHMENT_COUNT + 1)
    )
    messages = [{"role": "user", "content": "hello"}]

    with pytest.raises(HomeAssistantError, match="Too many attachments"):
        await _add_attachments(monkeypatch, [user], messages, "chat_completions")

    assert messages == [{"role": "user", "content": "hello"}]


@pytest.mark.asyncio
async def test_async_add_attachments_builds_responses_image_pdf_and_text(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Responses mode emits each supported provider attachment item shape."""
    image = tmp_path / "photo.png"
    image.write_bytes(b"png-data")
    pdf = tmp_path / "notes.pdf"
    pdf.write_bytes(b"pdf-data")
    text = tmp_path / "context.txt"
    text.write_text("plain text", encoding="utf-8")
    user = _FakeUserContent(
        attachments=[
            SimpleNamespace(path=str(image)),
            SimpleNamespace(path=str(pdf)),
            SimpleNamespace(path=str(text)),
        ]
    )
    messages = [{"role": "user", "content": "question"}]

    hass = await _add_attachments(
        monkeypatch,
        [user],
        messages,
        entity.API_MODE_RESPONSES,
    )

    assert hass.executor_calls == 1
    content = messages[0]["content"]
    assert isinstance(content, list)
    assert content[0] == {"type": "input_text", "text": "question"}
    assert content[1]["type"] == "input_image"
    assert content[1]["image_url"] == "data:image/png;base64,cG5nLWRhdGE="
    assert content[2] == {
        "type": "input_file",
        "filename": "notes.pdf",
        "file_data": "data:application/pdf;base64,cGRmLWRhdGE=",
    }
    assert content[3] == {
        "type": "input_text",
        "text": "\n\n--- context.txt ---\nplain text",
    }


@pytest.mark.asyncio
async def test_async_add_attachments_builds_chat_items_and_extends_existing_content(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Chat mode supports both string content conversion and existing part lists."""
    image = tmp_path / "photo.png"
    image.write_bytes(b"png-data")
    text = tmp_path / "context.txt"
    text.write_text("plain text", encoding="utf-8")

    messages = [{"role": "user", "content": "question"}]
    await _add_attachments(
        monkeypatch,
        [_FakeUserContent(attachments=[SimpleNamespace(path=str(image))])],
        messages,
        "chat_completions",
    )
    assert messages[0]["content"] == [
        {"type": "text", "text": "question"},
        {
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64,cG5nLWRhdGE="},
        },
    ]

    existing = [{"type": "text", "text": "already-parted"}]
    messages = [{"role": "user", "content": existing}]
    await _add_attachments(
        monkeypatch,
        [_FakeUserContent(attachments=[SimpleNamespace(path=str(text))])],
        messages,
        "chat_completions",
    )
    assert messages[0]["content"] == [
        {"type": "text", "text": "already-parted"},
        {"type": "text", "text": "\n\n--- context.txt ---\nplain text"},
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["missing", "directory", "unsupported"])
async def test_async_add_attachments_rejects_invalid_local_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    kind: str,
) -> None:
    """Invalid paths and unsupported media fail before message mutation."""
    if kind == "missing":
        path = tmp_path / "missing.txt"
        match = "does not exist"
    elif kind == "directory":
        path = tmp_path / "folder.txt"
        path.mkdir()
        match = "not a file"
    else:
        path = tmp_path / "payload.bin"
        path.write_bytes(b"binary")
        match = "Unsupported attachment type"

    messages = [{"role": "user", "content": "question"}]
    user = _FakeUserContent(attachments=[SimpleNamespace(path=str(path))])

    with pytest.raises(HomeAssistantError, match=match):
        await _add_attachments(
            monkeypatch,
            [user],
            messages,
            entity.API_MODE_RESPONSES,
        )

    assert messages == [{"role": "user", "content": "question"}]
