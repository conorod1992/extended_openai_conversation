"""Reusable SDK-wire fault transport for nightly public Assist campaigns.

Only the outbound HTTP send is replaced. EOAI's real agent, OpenAI SDK request
serialization, streaming parser, Home Assistant services and stores remain live.
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
import json
from typing import Any, Literal

import httpx

from tests_real_ha.test_provider_wire_e2e import _raw_client


@dataclass(frozen=True)
class WireStep:
    kind: Literal["sse", "http", "transport", "stream_break"]
    body: bytes = b""
    status: int = 200
    error: str = ""
    delay: float = 0.0


class _BrokenStream(httpx.AsyncByteStream):
    def __init__(self, prefix: bytes, request: httpx.Request) -> None:
        self.prefix = prefix
        self.request = request

    async def __aiter__(self):
        yield self.prefix
        raise httpx.ReadError("provider stream disconnected", request=self.request)


class ProviderFaultTransport:
    """Consume deterministic wire steps and retain every serialized request."""

    def __init__(self, steps: list[WireStep]) -> None:
        self.steps = deque(steps)
        self.requests: list[dict[str, Any]] = []

    def install(self, monkeypatch: Any, agent: Any) -> None:
        client = _raw_client(agent)
        # Disable SDK automatic retries for phase-specific outcomes. The test
        # explicitly sends another public Assist turn to prove recovery.
        monkeypatch.setattr(client, "max_retries", 0)
        monkeypatch.setattr(client._client, "send", self.send)

    async def send(
        self, request: httpx.Request, *args: Any, **kwargs: Any
    ) -> httpx.Response:
        del args, kwargs
        self.requests.append(
            {"path": request.url.path, "body": json.loads(request.content.decode())}
        )
        assert self.steps, "Unexpected extra provider request"
        step = self.steps.popleft()
        if step.delay:
            await asyncio.sleep(step.delay)
        if step.kind == "transport":
            errors: dict[str, type[httpx.RequestError]] = {
                "dns": httpx.ConnectError,
                "refused": httpx.ConnectError,
                "connect_timeout": httpx.ConnectTimeout,
                "read_timeout": httpx.ReadTimeout,
                "tls": httpx.ConnectError,
                "before_headers": httpx.RemoteProtocolError,
            }
            detail = {
                "dns": "Name or service not known",
                "refused": "Connection refused",
                "connect_timeout": "Connection timed out",
                "read_timeout": "Timed out waiting for provider headers",
                "tls": "SSL certificate verify failed",
                "before_headers": "Server disconnected before response headers",
            }[step.error]
            raise errors[step.error](detail, request=request)
        if step.kind == "http":
            return httpx.Response(
                step.status,
                headers={"content-type": "application/json"},
                json={
                    "error": {
                        "message": f"seeded provider HTTP {step.status}",
                        "type": "provider_fault_acceptance",
                        "code": f"fault_{step.status}",
                    }
                },
                request=request,
            )
        if step.kind == "stream_break":
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                stream=_BrokenStream(step.body, request),
                request=request,
            )
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=step.body,
            request=request,
        )
