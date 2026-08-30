"""Regression coverage for bounded REST and Scrape response bodies."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from homeassistant.exceptions import HomeAssistantError

from custom_components.extended_openai_conversation_responses.functions import web


class _FakeContent:
    def __init__(self, body: bytes, *, complete: bool = False) -> None:
        self.body = body
        self.complete = complete
        self.requested: list[int] = []

    async def readexactly(self, size: int) -> bytes:
        self.requested.append(size)
        if self.complete:
            return self.body
        raise asyncio.IncompleteReadError(partial=self.body, expected=size)


class _FakeResponse:
    def __init__(
        self,
        body: bytes,
        *,
        content_length: int | None = None,
        complete: bool = False,
    ) -> None:
        self.content = _FakeContent(body, complete=complete)
        self.content_length = content_length
        self.charset = "utf-8"
        self.headers: dict[str, str] = {}
        self.status = 200


class _FakeRequestContext:
    def __init__(self, response: _FakeResponse) -> None:
        self.response = response

    async def __aenter__(self) -> _FakeResponse:
        return self.response

    async def __aexit__(self, *args: Any) -> None:
        return None


class _FakeSession:
    def __init__(self, response: _FakeResponse) -> None:
        self.response = response
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def request(self, *args: Any, **kwargs: Any) -> _FakeRequestContext:
        self.calls.append((args, kwargs))
        return _FakeRequestContext(self.response)


def _rest_config() -> dict[str, Any]:
    return {
        "resource": "https://example.invalid/data",
        "method": "GET",
        "verify_ssl": True,
        "timeout": 10,
        "encoding": "utf-8",
    }


async def test_rest_data_stream_read_is_bounded(hass, monkeypatch) -> None:
    """Chunked/unknown-length responses abort after max+1 decoded bytes."""
    response = _FakeResponse(b"123456", complete=True)
    session = _FakeSession(response)
    monkeypatch.setattr(web, "MAX_REMOTE_RESPONSE_BYTES", 5)
    monkeypatch.setattr(web, "async_get_clientsession", lambda *_a, **_kw: session)

    rest_data = web.get_rest_data(hass, _rest_config(), {})

    with pytest.raises(HomeAssistantError, match="safety limit of 5 bytes"):
        await rest_data.async_update()

    assert response.content.requested == [6]
    assert len(session.calls) == 1


async def test_rest_data_rejects_oversized_content_length_before_read(
    hass, monkeypatch
) -> None:
    """A trustworthy oversized Content-Length can fail before body materialization."""
    response = _FakeResponse(b"ignored", content_length=6)
    session = _FakeSession(response)
    monkeypatch.setattr(web, "MAX_REMOTE_RESPONSE_BYTES", 5)
    monkeypatch.setattr(web, "async_get_clientsession", lambda *_a, **_kw: session)

    rest_data = web.get_rest_data(hass, _rest_config(), {})

    with pytest.raises(HomeAssistantError, match="safety limit of 5 bytes"):
        await rest_data.async_update()

    assert response.content.requested == []


async def test_rest_data_preserves_normal_text_and_reuses_bounded_body(
    hass, monkeypatch
) -> None:
    """Normal REST decoding still works and repeated decoding never re-reads the stream."""
    response = _FakeResponse(b"hello")
    session = _FakeSession(response)
    monkeypatch.setattr(web, "MAX_REMOTE_RESPONSE_BYTES", 5)
    monkeypatch.setattr(web, "async_get_clientsession", lambda *_a, **_kw: session)

    rest_data = web.get_rest_data(hass, _rest_config(), {})
    await rest_data.async_update()

    assert rest_data.data == "hello"
    assert response.content.requested == [6]

    bounded_response = web._BoundedResponse(response, 5)
    assert await bounded_response.text() == "hello"
    assert await bounded_response.text(encoding="utf-8") == "hello"
    assert response.content.requested == [6, 6]
