"""Exercise HTTP failures and decoding through HA's real REST implementation."""

import asyncio
from compression import zstd
import gzip
import zlib

import aiohttp
from aiohttp import web as http_web
import brotli
import pytest

from custom_components.extended_openai_conversation_responses.functions import web
from custom_components.extended_openai_conversation_responses.functions.composite import (
    CompositeFunction,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.template import Template
from tests.test_remote_response_bounds import _FakeResponse, _FakeSession, _rest_config


@pytest.fixture
async def rest_server(hass, socket_enabled, aiohttp_server, monkeypatch):
    """Use an actual local HTTP server and the real HA RestData request path."""
    app = http_web.Application()

    async def respond(request):
        status = int(request.match_info["status"])
        kind = request.match_info["kind"]
        body = (
            b'{"answer":42,"private":"secret"}' if kind == "json" else b"<p>secret</p>"
        )
        return http_web.Response(
            status=status,
            body=b"" if status == 204 else body,
            content_type="application/json" if kind == "json" else "text/html",
        )

    app.router.add_get("/{status}/{kind}", respond)
    server = await aiohttp_server(app)
    async with aiohttp.ClientSession() as session:
        monkeypatch.setattr(web, "async_get_clientsession", lambda *_a, **_kw: session)
        yield lambda status, kind: {
            **_rest_config(),
            "type": "rest",
            "resource": str(server.make_url(f"/{status}/{kind}")),
        }


@pytest.mark.parametrize("status", [400, 401, 404, 500, 304])
@pytest.mark.parametrize("kind", ["json", "html"])
async def test_http_failure_stops_composite(hass, rest_server, status, kind):
    # A template after the REST step raises if it is reached, proving the
    # composite stops for the status failure rather than interpreting the body.
    follow = Template("{{ missing.child }}", hass)
    config = {
        "sequence": [
            rest_server(status, kind),
            {"type": "template", "value_template": follow},
        ]
    }
    with pytest.raises(HomeAssistantError, match=f"HTTP {status}") as caught:
        await CompositeFunction().execute(hass, config, {}, None, [])
    assert "secret" not in str(caught.value)
    assert "http://" not in str(caught.value)


@pytest.mark.parametrize("status", [200, 204])
async def test_http_success_controls(hass, rest_server, status):
    result = await web.RestFunction().execute(
        hass, rest_server(status, "json"), {}, None, []
    )
    assert result == ('{"answer":42,"private":"secret"}' if status == 200 else "")


async def test_status_failure_closes_request_without_reading_body():
    response = _FakeResponse(b"secret")
    response.status = 500
    exits = []

    class TrackingContext:
        async def __aenter__(self):
            return response

        async def __aexit__(self, *args):
            exits.append(args)

    with pytest.raises(HomeAssistantError, match="HTTP 500"):
        async with web._BoundedRequestContext(TrackingContext(), 10) as bounded:
            await bounded.text()
    assert len(exits) == 1
    assert exits[0][0] is HomeAssistantError
    assert response.content.requested == []


async def test_rest_template_failure_stops_composite(hass, rest_server):
    config = {
        "sequence": [
            {
                **rest_server(200, "json"),
                "value_template": Template("{{ value_json.missing.child }}", hass),
            },
            {"type": "template", "value_template": Template("{{ later.child }}", hass)},
        ]
    }
    with pytest.raises(HomeAssistantError, match="missing"):
        await CompositeFunction().execute(hass, config, {}, None, [])


@pytest.mark.parametrize(
    "kind,template,expected",
    [
        ("json", "{{ value_json.answer + increment }}", "43"),
        ("html", "{{ value }}", "<p>secret</p>"),
    ],
)
async def test_rest_value_variables(hass, rest_server, kind, template, expected):
    result = await web.RestFunction().execute(
        hass,
        {**rest_server(200, kind), "value_template": Template(template, hass)},
        {"increment": 1},
        None,
        [],
    )
    assert result == expected


@pytest.mark.parametrize("scrape", [False, True])
async def test_charset_fallback_reuses_bounded_body(hass, monkeypatch, scrape):
    hass.loop = asyncio.get_running_loop()
    hass.is_stopping = False
    response = _FakeResponse(b"<p>caf\xe9</p>" if scrape else b"caf\xe9")
    response.headers = {"Content-Type": "text/html; charset=utf-8"}
    session = _FakeSession(response)
    monkeypatch.setattr(web, "async_get_clientsession", lambda *_a, **_kw: session)
    config = {**_rest_config(), "encoding": "windows-1252"}
    function = web.RestFunction()
    if scrape:
        function = web.ScrapeFunction()
        config["sensor"] = [{"select": "p"}]
    assert await function.execute(hass, config, {}, None, []) == "café"
    assert len(session.calls) == 1
    assert response.content.requested == [web.MAX_REMOTE_RESPONSE_BYTES + 1]


async def test_unrecoverable_encoding_is_explicit_failure(hass, monkeypatch):
    response = _FakeResponse(b"\xff")
    session = _FakeSession(response)
    monkeypatch.setattr(web, "async_get_clientsession", lambda *_a, **_kw: session)
    with pytest.raises(HomeAssistantError, match="cannot be decoded"):
        await web.RestFunction().execute(hass, _rest_config(), {}, None, [])
    assert len(response.content.requested) == 1


@pytest.mark.parametrize(
    "encoding,compress",
    [
        ("br", brotli.compress),
        ("zstd", zstd.compress),
        ("gzip", gzip.compress),
        ("deflate", zlib.compress),
    ],
)
def test_compressed_bounds_and_integrity(encoding, compress):
    for size in (0, 63, 64):
        assert (
            web._decode_compressed_body(compress(b"x" * size), encoding, 64)
            == b"x" * size
        )
    with pytest.raises(HomeAssistantError, match="safety limit"):
        web._decode_compressed_body(compress(b"x" * 1_000_000), encoding, 64)
    with pytest.raises(aiohttp.ClientPayloadError, match="compressed response"):
        web._decode_compressed_body(compress(b"near-limit text")[:-1], encoding, 64)


def test_brotli_output_allocations_are_bounded(monkeypatch):
    original = brotli.Decompressor
    outputs = []
    budgets = []

    class TrackingDecoder:
        def __init__(self):
            self.decoder = original()

        def process(self, data, budget):
            budgets.append(budget)
            part = self.decoder.process(data, budget)
            outputs.append(len(part))
            return part

        def is_finished(self):
            return self.decoder.is_finished()

        def can_accept_more_data(self):
            return self.decoder.can_accept_more_data()

    monkeypatch.setattr(brotli, "Decompressor", TrackingDecoder)
    with pytest.raises(HomeAssistantError, match="safety limit"):
        web._decode_compressed_body(brotli.compress(b"x" * 1_000_000), "br", 64)
    assert budgets == [65]
    assert sum(outputs) <= 32 * 1024
    # Valid output spans several bounded drain calls.
    outputs.clear()
    budgets.clear()
    assert (
        web._decode_compressed_body(brotli.compress(b"x" * 100_000), "br", 100_000)
        == b"x" * 100_000
    )
    assert len(outputs) > 1
    assert max(budgets) <= web._DECODE_CHUNK_BYTES


def test_zstd_output_allocations_and_multiple_frames(monkeypatch):
    original = web._StdlibZstdDecompressor
    outputs = []

    class TrackingDecoder:
        def __init__(self):
            self.decoder = original()

        def decompress(self, data, budget):
            part = self.decoder.decompress(data, budget)
            outputs.append(len(part))
            return part

        def __getattr__(self, name):
            return getattr(self.decoder, name)

    monkeypatch.setattr(web, "_StdlibZstdDecompressor", TrackingDecoder)
    with pytest.raises(HomeAssistantError, match="safety limit"):
        web._decode_compressed_body(zstd.compress(b"x" * 1_000_000), "zstd", 64)
    assert outputs == [65]
    body = zstd.compress(b"first") + zstd.compress(b"second")
    assert web._decode_compressed_body(body, "zstd", 11) == b"firstsecond"
    with pytest.raises(aiohttp.ClientPayloadError, match="Incomplete"):
        web._decode_compressed_body(body[:-1], "zstd", 11)


def test_brotli_fails_closed_when_support_or_bounded_codec_is_missing(monkeypatch):
    compression = web.aiohttp.compression_utils
    body = brotli.compress(b"safe")

    monkeypatch.setattr(compression, "HAS_BROTLI", False)
    with pytest.raises(HomeAssistantError, match="decoding is unavailable"):
        web._decode_compressed_body(body, "br", 32)

    monkeypatch.setattr(compression, "HAS_BROTLI", True)
    monkeypatch.setattr(compression.brotli, "Decompressor", object)
    with pytest.raises(HomeAssistantError, match="bounded codec"):
        web._decode_compressed_body(body, "br", 32)


def test_brotli_and_zstd_report_malformed_frames_as_payload_errors():
    with pytest.raises(aiohttp.ClientPayloadError, match="Malformed compressed"):
        web._decode_compressed_body(b"not a brotli frame", "br", 128)
    with pytest.raises(aiohttp.ClientPayloadError, match="Malformed compressed"):
        web._decode_compressed_body(b"not a zstd frame", "zstd", 128)


@pytest.mark.parametrize("encoding", ["gzip", "deflate", "zstd"])
def test_compressed_decoders_reject_empty_and_malformed_streams(encoding):
    with pytest.raises(aiohttp.ClientPayloadError, match="Empty compressed response"):
        web._decode_compressed_body(b"", encoding, 128)

    if encoding in {"gzip", "deflate"}:
        with pytest.raises(aiohttp.ClientPayloadError, match="Malformed compressed"):
            web._decode_compressed_body(b"not a compressed stream", encoding, 128)


async def test_bounded_response_sanitizes_unknown_http_status_and_text_encoding():
    response = _FakeResponse(b"secret")
    response.status = 599
    with pytest.raises(HomeAssistantError, match="HTTP 599 Unsuccessful response"):
        await web._BoundedResponse(response, 32).read()
    assert response.content.requested == []

    response.status = 200
    response.content.body = b"\xff"
    with pytest.raises(HomeAssistantError, match="cannot be decoded"):
        await web._BoundedResponse(response, 32).text(encoding="ascii")
