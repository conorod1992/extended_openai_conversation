"""Run the shipped HA frontend through a real local HTTPS reverse proxy."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import ipaddress
import json
from pathlib import Path
import ssl

from aiohttp import ClientSession, WSMsgType, web
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from tests_real_ha.test_browser_backend_acceptance import (
    _run_playwright,
    real_ha_shell as real_ha_shell,
)

_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


def _server_tls(tmp_path: Path) -> ssl.SSLContext:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "EOAI HTTPS acceptance")]
    )
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                ]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert = tmp_path / "proxy-cert.pem"
    private = tmp_path / "proxy-key.pem"
    cert.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    private.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, private)
    return context


def _headers(source, *, request: bool) -> dict[str, str]:
    excluded = _HOP_BY_HOP | ({"host", "content-length"} if request else {"content-length"})
    return {
        key: value
        for key, value in source.items()
        if key.lower() not in excluded
    }


async def _start_proxy(upstream: str, tmp_path: Path):
    session = ClientSession(auto_decompress=False)
    app = web.Application()

    async def handler(request: web.Request) -> web.StreamResponse:
        path = request.rel_url.raw_path_qs
        target = upstream.rstrip("/") + path
        if request.headers.get("Upgrade", "").lower() == "websocket":
            downstream = web.WebSocketResponse()
            await downstream.prepare(request)
            ws_target = target.replace("http://", "ws://", 1).replace(
                "https://", "wss://", 1
            )
            upstream_ws = await session.ws_connect(ws_target)

            async def to_upstream():
                async for message in downstream:
                    if message.type is WSMsgType.TEXT:
                        await upstream_ws.send_str(message.data)
                    elif message.type is WSMsgType.BINARY:
                        await upstream_ws.send_bytes(message.data)
                    elif message.type in {WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR}:
                        break

            async def to_browser():
                async for message in upstream_ws:
                    if message.type is WSMsgType.TEXT:
                        await downstream.send_str(message.data)
                    elif message.type is WSMsgType.BINARY:
                        await downstream.send_bytes(message.data)
                    elif message.type in {WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR}:
                        break

            tasks = {
                asyncio.create_task(to_upstream()),
                asyncio.create_task(to_browser()),
            }
            done, pending = await asyncio.wait(
                tasks, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            await upstream_ws.close()
            for task in done:
                task.result()
            await downstream.close()
            return downstream

        body = await request.read()
        async with session.request(
            request.method,
            target,
            headers=_headers(request.headers, request=True),
            data=body if body else None,
            allow_redirects=False,
        ) as response:
            payload = await response.read()
            headers = _headers(response.headers, request=False)
            if location := headers.get("Location"):
                headers["Location"] = location.replace(
                    upstream.rstrip("/"),
                    f"https://{request.host}",
                    1,
                )
            return web.Response(
                status=response.status,
                reason=response.reason,
                headers=headers,
                body=payload,
            )

    app.router.add_route("*", "/{tail:.*}", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(
        runner,
        "127.0.0.1",
        0,
        ssl_context=_server_tls(tmp_path),
    )
    await site.start()
    assert site._server is not None
    port = site._server.sockets[0].getsockname()[1]
    return runner, session, f"https://127.0.0.1:{port}"


async def test_management_panel_survives_real_https_reverse_proxy(
    real_ha_shell,
    tmp_path: Path,
) -> None:
    """Load, save and transfer through HTTPS + WSS rather than direct HA HTTP."""
    shell = real_ha_shell
    upstream = shell["env"]["REAL_HA_FRONTEND_URL"]
    runner, session, proxy = await _start_proxy(upstream, tmp_path)
    auth = json.loads(shell["env"]["REAL_HA_FRONTEND_AUTH"])
    auth["hassUrl"] = proxy
    try:
        await _run_playwright(
            repo_root=Path(__file__).resolve().parents[1],
            spec="tests_browser/real-ha-https-proxy.spec.mjs",
            config="playwright.real-ha-https-proxy.config.mjs",
            env={
                **shell["env"],
                "REAL_HA_FRONTEND_URL": proxy,
                "REAL_HA_FRONTEND_AUTH": json.dumps(auth),
                "EOAI_HTTPS_PROXY": "1",
            },
            failure_label="Genuine HA HTTPS reverse-proxy acceptance failed",
        )
    finally:
        await session.close()
        await runner.cleanup()
