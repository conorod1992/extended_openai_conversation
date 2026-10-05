"""Serve a disposable populated Home Assistant instance for Companion-app acceptance."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import signal
import sys
import threading

DOMAIN = "extended_openai_conversation_responses"
USERNAME = "eoai-companion"
PASSWORD = "eoai-companion-password"


class Provider(BaseHTTPRequestHandler):
    def log_message(self, *args):
        del args

    def _reply(self, status, value, content_type="application/json"):
        body = value.encode() if isinstance(value, str) else json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/v1/models":
            return self._reply(
                200,
                {
                    "object": "list",
                    "data": [
                        {
                            "id": "gpt-5.6",
                            "object": "model",
                            "created": 0,
                            "owned_by": "companion-app-acceptance",
                        }
                    ],
                },
            )
        return self._reply(404, {})

    def do_POST(self):
        if self.path != "/v1/chat/completions":
            return self._reply(404, {})
        chunk = {
            "id": "companion-app",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "gpt-5.6",
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "role": "assistant",
                        "content": "Companion app acceptance healthy.",
                    },
                    "finish_reason": "stop",
                }
            ],
        }
        return self._reply(
            200,
            "data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n",
            "text/event-stream",
        )


async def main(config_dir: Path, component: Path, ready: Path) -> None:
    from homeassistant import bootstrap, runner
    from homeassistant.auth.const import GROUP_ID_ADMIN
    from homeassistant.auth.providers.homeassistant import async_get_provider
    from homeassistant.components.onboarding import STORAGE_KEY, STORAGE_VERSION
    from homeassistant.components.onboarding.const import STEPS
    from homeassistant.config_entries import SOURCE_USER
    from homeassistant.const import CONF_API_KEY, CONF_NAME
    from homeassistant.data_entry_flow import FlowResultType

    integration = config_dir / "custom_components" / DOMAIN
    integration.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(component, integration)
    (config_dir / "configuration.yaml").write_text(
        "homeassistant:\n"
        "  name: EOAI Companion Acceptance\n"
        "http:\n"
        "  server_host: 0.0.0.0\n"
        "  server_port: 8123\n"
        "frontend:\n"
        "api:\n"
        "websocket_api:\n"
        "mobile_app:\n"
        "recorder:\n"
        "onboarding:\n",
        encoding="utf-8",
    )

    provider_server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    provider_thread = threading.Thread(
        target=provider_server.serve_forever, daemon=True
    )
    provider_thread.start()
    endpoint = f"http://127.0.0.1:{provider_server.server_port}"

    # Onboarding reads this once during bootstrap. Saving it after setup leaves
    # the active views reporting an incomplete setup and redirects app login.
    storage_dir = config_dir / ".storage"
    storage_dir.mkdir(exist_ok=True)
    (storage_dir / STORAGE_KEY).write_text(
        json.dumps(
            {
                "version": STORAGE_VERSION,
                "minor_version": 1,
                "key": STORAGE_KEY,
                "data": {"done": list(STEPS)},
            }
        ),
        encoding="utf-8",
    )

    sys.path.insert(0, str(config_dir))
    hass = await bootstrap.async_setup_hass(
        runner.RuntimeConfig(config_dir=str(config_dir), skip_pip=False)
    )
    assert hass is not None

    provider = async_get_provider(hass)
    await provider.async_initialize()
    await provider.async_add_auth(USERNAME, PASSWORD)
    owner = await hass.auth.async_create_user(
        "EOAI Companion Acceptance",
        group_ids=[GROUP_ID_ADMIN],
    )
    assert owner.is_owner
    credentials = await provider.async_get_or_create_credentials({"username": USERNAME})
    await hass.auth.async_link_user(owner, credentials)

    # Start HA before creating EOAI. Several declared HA dependencies (notably
    # Recorder) establish bootstrap-owned runtime data during startup and must not
    # be forced through config-entry dependency setup prematurely.
    await hass.async_start()
    await hass.async_block_till_done()

    const = __import__(
        f"custom_components.{DOMAIN}.const",
        fromlist=["CONF_BASE_URL"],
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Companion acceptance assistant",
            CONF_API_KEY: "sk-companion-acceptance",
            const.CONF_BASE_URL: endpoint + "/v1",
            const.CONF_SKIP_AUTHENTICATION: False,
            const.CONF_API_PROVIDER: "openai",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY, result
    await hass.async_block_till_done()
    entry = result["result"]
    assert entry.state.value == "loaded"

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    ready.write_text(
        json.dumps(
            {
                "username": USERNAME,
                "password": PASSWORD,
                "url": "http://10.0.2.2:8123",
                "entry_id": entry.entry_id,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    try:
        await stop.wait()
    finally:
        await hass.async_stop()
        provider_server.shutdown()
        provider_server.server_close()
        provider_thread.join()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--component", type=Path, required=True)
    parser.add_argument("--ready", type=Path, required=True)
    args = parser.parse_args()
    args.config.mkdir(parents=True, exist_ok=True)
    asyncio.run(
        main(
            args.config.resolve(),
            args.component.resolve(),
            args.ready.resolve(),
        )
    )
