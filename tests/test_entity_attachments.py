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


def _attachment(path: object, mime_type: str | None = None) -> object:
    """Build the attachment shape consumed by entity attachment handling."""
    return SimpleNamespace(path=str(path), mime_type=mime_type)


@pytest.mark.parametrize("api_mode", ["chat_completions", "responses"])
async def test_earlier_turn_attachment_survives_text_only_followup(monkeypatch, tmp_path, api_mode) -> None:
    photo = tmp_path / "photo.png"
    photo.write_bytes(b"original image")
    earlier = _FakeUserContent(attachments=[_attachment(photo)])
    followup = _FakeUserContent()
    messages = [{"role": "user", "content": "What is here?"}, {"role": "assistant", "content": "A photo."}, {"role": "user", "content": "What is in the corner?"}]
    await _add_attachments(monkeypatch, [earlier, object(), followup], messages, api_mode)
    assert isinstance(messages[0]["content"], list)
    assert messages[-1]["content"] == "What is in the corner?"


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
        attachments=[_attachment("unused")] * (entity.MAX_ATTACHMENT_COUNT + 1)
    )
    messages = [{"role": "user", "content": "hello"}]

    with pytest.raises(
        HomeAssistantError,
        match=rf"At most {entity.MAX_ATTACHMENT_COUNT} attachments can be sent",
    ):
        await _add_attachments(monkeypatch, [user], messages, "chat_completions")

    assert messages == [{"role": "user", "content": "hello"}]


@pytest.mark.asyncio
async def test_async_add_attachments_enforces_count_limit_across_retained_turns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The request-wide attachment count cannot be bypassed via conversation history."""
    earlier = _FakeUserContent(
        attachments=[_attachment("unused")] * 6
    )
    later = _FakeUserContent(
        attachments=[_attachment("unused")] * 5
    )
    messages = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "reply"},
        {"role": "user", "content": "second"},
    ]

    with pytest.raises(
        HomeAssistantError,
        match=rf"At most {entity.MAX_ATTACHMENT_COUNT} attachments can be sent",
    ):
        await _add_attachments(
            monkeypatch,
            [earlier, object(), later],
            messages,
            "chat_completions",
        )

    assert messages[0]["content"] == "first"
    assert messages[-1]["content"] == "second"


@pytest.mark.asyncio
async def test_async_add_attachments_carries_byte_budget_across_retained_turns(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """A later historical attachment sees bytes already consumed by earlier turns."""
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    first.write_bytes(b"x")
    second.write_bytes(b"x")
    totals: list[int] = []

    def bounded_read(path, total_bytes=0):
        totals.append(total_bytes)
        if total_bytes + 6 > 10:
            raise HomeAssistantError("combined request limit")
        return b"x" * 6

    monkeypatch.setattr(entity, "read_bounded_local_file", bounded_read)
    messages = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "reply"},
        {"role": "user", "content": "second"},
    ]

    with pytest.raises(HomeAssistantError, match="combined request limit"):
        await _add_attachments(
            monkeypatch,
            [
                _FakeUserContent(attachments=[_attachment(first, "image/png")]),
                object(),
                _FakeUserContent(attachments=[_attachment(second, "image/png")]),
            ],
            messages,
            "chat_completions",
        )

    assert totals == [0, 6]


@pytest.mark.asyncio
async def test_async_add_attachments_builds_responses_image_and_pdf(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Responses mode emits the supported image and PDF provider item shapes."""
    image = tmp_path / "photo.png"
    image.write_bytes(b"png-data")
    pdf = tmp_path / "notes.pdf"
    pdf.write_bytes(b"pdf-data")
    user = _FakeUserContent(
        attachments=[
            _attachment(image),
            _attachment(pdf),
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
    assert content == [
        {"type": "input_text", "text": "question"},
        {
            "type": "input_image",
            "image_url": "data:image/png;base64,cG5nLWRhdGE=",
            "detail": "auto",
        },
        {
            "type": "input_file",
            "filename": "notes.pdf",
            "file_data": "data:application/pdf;base64,cGRmLWRhdGE=",
        },
    ]


@pytest.mark.asyncio
async def test_async_add_attachments_builds_chat_image_and_honors_explicit_mime_type(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Chat mode emits image parts and lets attachment metadata override guessing."""
    image = tmp_path / "photo.unknown"
    image.write_bytes(b"png-data")
    messages = [{"role": "user", "content": "question"}]

    await _add_attachments(
        monkeypatch,
        [_FakeUserContent(attachments=[_attachment(image, "image/png")])],
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


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["missing", "directory", "unknown", "unsupported"])
async def test_async_add_attachments_rejects_invalid_local_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    kind: str,
) -> None:
    """Invalid paths and unsupported media fail before message mutation."""
    if kind == "missing":
        path = tmp_path / "missing.txt"
        attachment = _attachment(path)
        match = "does not exist"
    elif kind == "directory":
        path = tmp_path / "folder.txt"
        path.mkdir()
        attachment = _attachment(path)
        match = "not a file"
    elif kind == "unknown":
        path = tmp_path / "payload.unknown"
        path.write_bytes(b"binary")
        attachment = _attachment(path)
        match = "Unable to determine attachment type"
    else:
        path = tmp_path / "payload.bin"
        path.write_bytes(b"binary")
        attachment = _attachment(path, "application/octet-stream")
        match = "Unsupported attachment"

    messages = [{"role": "user", "content": "question"}]
    user = _FakeUserContent(attachments=[attachment])

    with pytest.raises(HomeAssistantError, match=match):
        await _add_attachments(
            monkeypatch,
            [user],
            messages,
            entity.API_MODE_RESPONSES,
        )

    assert messages == [{"role": "user", "content": "question"}]


@pytest.mark.asyncio
async def test_async_add_attachments_requires_text_user_content(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Prepared attachments cannot be appended to already-multipart user content."""
    image = tmp_path / "photo.png"
    image.write_bytes(b"png-data")
    messages = [
        {
            "role": "user",
            "content": [{"type": "text", "text": "already-parted"}],
        }
    ]

    with pytest.raises(
        HomeAssistantError,
        match="Unable to attach files to non-text user content",
    ):
        await _add_attachments(
            monkeypatch,
            [_FakeUserContent(attachments=[_attachment(image)])],
            messages,
            "chat_completions",
        )


@pytest.mark.asyncio
async def test_async_add_attachments_creates_missing_user_message(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """A provider user message is synthesized if conversion produced none."""
    image = tmp_path / "photo.png"
    image.write_bytes(b"png-data")
    messages: list[dict[str, object]] = []

    await _add_attachments(
        monkeypatch,
        [_FakeUserContent(attachments=[_attachment(image)])],
        messages,
        entity.API_MODE_RESPONSES,
    )

    assert messages == [
        {
            "type": "message",
            "role": "user",
            "content": [
                {
                    "type": "input_image",
                    "image_url": "data:image/png;base64,cG5nLWRhdGE=",
                    "detail": "auto",
                }
            ],
        }
    ]
