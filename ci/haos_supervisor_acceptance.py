"""Drive a genuine Home Assistant OS + Supervisor VM acceptance journey."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tarfile
import threading
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import aiohttp

DOMAIN = "extended_openai_conversation_responses"
WS_COMMAND = f"{DOMAIN}/management"
MODEL = "gpt-5.6"
TOOL_NAME = "haos_marker"
TOOL_MARKER = "EOAI_HAOS_TOOL_RESULT_4D7C"
KNOWLEDGE_MARKER = "EOAI_HAOS_KNOWLEDGE_Q2M8"
CLIENT_ID = "http://127.0.0.1:8123/"
USERNAME = "eoai-haos"
PASSWORD = "EOAI-haos-acceptance-only-42"
PROVIDER_PORT = 18765
GUEST_PROVIDER_URL = f"http://10.0.2.2:{PROVIDER_PORT}/v1"


def _json_request(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    token: str | None = None,
    timeout: float = 10,
) -> Any:
    body = None
    headers: dict[str, str] = {}
    if payload is not None:
        body = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url, data=body, headers=headers, method=method)
    with urlopen(request, timeout=timeout) as response:
        raw = response.read()
    return json.loads(raw) if raw else {}


def _form_request(url: str, data: dict[str, str], *, timeout: float = 10) -> Any:
    request = Request(
        url,
        data=urlencode(data).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def _wait_http(base_url: str, *, token: str | None = None, timeout: float = 1200) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            if token:
                _json_request(f"{base_url}/api/", token=token, timeout=5)
            else:
                _json_request(f"{base_url}/api/onboarding", timeout=5)
            return
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as err:
            last_error = err
            time.sleep(3)
    raise TimeoutError(f"Home Assistant did not become ready: {last_error}")


class DeterministicProvider(BaseHTTPRequestHandler):
    """Tiny OpenAI-compatible provider reachable from QEMU's host gateway."""

    requests: list[dict[str, Any]] = []

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def _reply(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path == "/v1/models":
            self._reply(
                200,
                json.dumps(
                    {
                        "object": "list",
                        "data": [
                            {
                                "id": MODEL,
                                "object": "model",
                                "created": 0,
                                "owned_by": "eoai-haos-acceptance",
                            }
                        ],
                    }
                ).encode(),
                "application/json",
            )
            return
        self._reply(404, b"{}", "application/json")

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        type(self).requests.append({"path": self.path, "body": payload})

        if self.path != "/v1/chat/completions":
            self._reply(404, b"{}", "application/json")
            return

        serialized = json.dumps(payload, ensure_ascii=False)
        if TOOL_MARKER in serialized:
            delta = {
                "role": "assistant",
                "content": f"HA OS tool completed: {TOOL_MARKER}",
            }
            finish_reason = "stop"
        else:
            delta = {
                "role": "assistant",
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call-haos-marker",
                        "type": "function",
                        "function": {
                            "name": TOOL_NAME,
                            "arguments": "{}",
                        },
                    }
                ],
            }
            finish_reason = "tool_calls"

        chunk = {
            "id": "chatcmpl-haos",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": MODEL,
            "choices": [
                {
                    "index": 0,
                    "delta": delta,
                    "finish_reason": finish_reason,
                }
            ],
        }
        body = (
            f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n"
        ).encode()
        self._reply(200, body, "text/event-stream")


@asynccontextmanager
async def _provider_server():
    DeterministicProvider.requests = []
    server = ThreadingHTTPServer(("0.0.0.0", PROVIDER_PORT), DeterministicProvider)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


class HAWebSocket:
    """Minimal authenticated Home Assistant WebSocket client."""

    def __init__(self, base_url: str, token: str) -> None:
        self.url = base_url.replace("http://", "ws://").replace("https://", "wss://") + "/api/websocket"
        self.token = token
        self.session: aiohttp.ClientSession | None = None
        self.ws: aiohttp.ClientWebSocketResponse | None = None
        self.message_id = 0

    async def __aenter__(self) -> "HAWebSocket":
        self.session = aiohttp.ClientSession()
        self.ws = await self.session.ws_connect(self.url, heartbeat=20)
        greeting = await self.ws.receive_json()
        assert greeting["type"] == "auth_required"
        await self.ws.send_json({"type": "auth", "access_token": self.token})
        authenticated = await self.ws.receive_json()
        assert authenticated["type"] == "auth_ok", authenticated
        return self

    async def __aexit__(self, *_args: Any) -> None:
        if self.ws is not None:
            await self.ws.close()
        if self.session is not None:
            await self.session.close()

    async def call(self, message_type: str, **payload: Any) -> Any:
        assert self.ws is not None
        self.message_id += 1
        message_id = self.message_id
        await self.ws.send_json({"id": message_id, "type": message_type, **payload})
        while True:
            response = await self.ws.receive_json()
            if response.get("id") != message_id:
                continue
            assert response["type"] == "result", response
            assert response["success"] is True, response
            return response.get("result")


class HostSSH:
    """Root SSH into HA OS through the supported debug SSH port."""

    def __init__(self, key: Path, port: int) -> None:
        self.key = key
        self.port = port
        self.base = [
            "ssh",
            "-i",
            str(key),
            "-p",
            str(port),
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
            "-o",
            "ConnectTimeout=10",
            "root@127.0.0.1",
        ]

    def run(
        self,
        command: str,
        *,
        timeout: float = 120,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [*self.base, command],
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        if check and result.returncode != 0:
            raise AssertionError(
                f"HA OS SSH command failed: {command}\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
        return result

    def json(self, command: str, *, timeout: float = 120) -> Any:
        output = self.run(command, timeout=timeout).stdout.strip()
        return json.loads(output)


def _wait_ssh(ssh: HostSSH, timeout: float = 1200) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = ssh.run("true", timeout=15, check=False)
        if result.returncode == 0:
            return
        time.sleep(5)
    raise TimeoutError("HA OS debug SSH never became available")


def _install_candidate(ssh: HostSSH, repo_root: Path, work_dir: Path) -> str:
    source = repo_root / "custom_components" / DOMAIN
    archive = work_dir / "eoai-candidate.tar.gz"
    with tarfile.open(archive, "w:gz") as handle:
        handle.add(source, arcname=DOMAIN)

    upload = subprocess.run(
        [
            "ssh",
            "-i",
            str(ssh.key),
            "-p",
            str(ssh.port),
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
            "root@127.0.0.1",
            "cat > /tmp/eoai-candidate.tar.gz",
        ],
        input=archive.read_bytes(),
        capture_output=True,
        timeout=120,
        check=False,
    )
    assert upload.returncode == 0, (
        f"candidate upload failed:\n{upload.stdout!r}\n{upload.stderr.decode(errors='replace')}"
    )

    command = (
        "set -eu; "
        "CONFIG=$(docker inspect -f '{{range .Mounts}}{{if eq .Destination \"/config\"}}{{.Source}}{{end}}{{end}}' homeassistant); "
        'test -n "$CONFIG"; '
        f'rm -rf "$CONFIG/custom_components/{DOMAIN}"; '
        'mkdir -p "$CONFIG/custom_components"; '
        'tar -xzf /tmp/eoai-candidate.tar.gz -C "$CONFIG/custom_components"; '
        f'test -f "$CONFIG/custom_components/{DOMAIN}/manifest.json"; '
        f'python_marker=$(sha256sum "$CONFIG/custom_components/{DOMAIN}/manifest.json" | cut -d" " -f1); '
        'sync; printf "%s" "$python_marker"'
    )
    return ssh.run(command).stdout.strip()


def _supervisor_api(
    ssh: HostSSH,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    detached: bool = False,
    timeout: float = 180,
) -> Any:
    body = json.dumps(payload if payload is not None else {})
    py = (
        "import json,os,urllib.request;"
        f"url='http://supervisor{path}';"
        f"data={body!r}.encode() if {method!r} != 'GET' else None;"
        "req=urllib.request.Request(url,data=data,method="
        f"{method!r},headers={{'Authorization':'Bearer '+os.environ['SUPERVISOR_TOKEN'],"
        "'Content-Type':'application/json'});"
        f"resp=urllib.request.urlopen(req,timeout={max(1, int(timeout))});"
        "print(resp.read().decode())"
    )
    command = (
        "docker exec "
        + ("-d " if detached else "")
        + "homeassistant python -c "
        + shlex.quote(py)
    )
    result = ssh.run(command, timeout=timeout, check=not detached)
    if detached:
        return {}
    text = result.stdout.strip()
    return json.loads(text) if text else {}


def _onboard(base_url: str) -> str:
    status = _json_request(f"{base_url}/api/onboarding")
    if all(item["done"] for item in status):
        raise AssertionError("fresh HA OS image was unexpectedly already onboarded")

    created = _json_request(
        f"{base_url}/api/onboarding/users",
        method="POST",
        payload={
            "name": "EOAI HAOS Acceptance",
            "username": USERNAME,
            "password": PASSWORD,
            "client_id": CLIENT_ID,
            "language": "en",
        },
        timeout=60,
    )
    token_data = _form_request(
        f"{base_url}/auth/token",
        {
            "client_id": CLIENT_ID,
            "grant_type": "authorization_code",
            "code": created["auth_code"],
        },
    )
    token = token_data["access_token"]

    _json_request(
        f"{base_url}/api/onboarding/core_config",
        method="POST",
        payload={},
        token=token,
        timeout=120,
    )
    _json_request(
        f"{base_url}/api/onboarding/analytics",
        method="POST",
        payload={},
        token=token,
        timeout=60,
    )
    _json_request(
        f"{base_url}/api/onboarding/integration",
        method="POST",
        payload={"client_id": CLIENT_ID, "redirect_uri": CLIENT_ID},
        token=token,
        timeout=60,
    )
    status = _json_request(f"{base_url}/api/onboarding")
    assert all(item["done"] for item in status), status
    return token


def _create_eoai_entry(base_url: str, token: str) -> str:
    flow = _json_request(
        f"{base_url}/api/config/config_entries/flow",
        method="POST",
        payload={"handler": DOMAIN},
        token=token,
        timeout=60,
    )
    assert flow["type"] == "form", flow
    completed = _json_request(
        f"{base_url}/api/config/config_entries/flow/{flow['flow_id']}",
        method="POST",
        payload={
            "name": "HA OS Supervisor Acceptance",
            "api_key": "sk-haos-acceptance",
            "base_url": GUEST_PROVIDER_URL,
            "skip_authentication": True,
            "api_provider": "openai",
        },
        token=token,
        timeout=60,
    )
    assert completed["type"] == "create_entry", completed
    return completed["result"]["entry_id"]


async def _configure_and_seed(
    base_url: str,
    token: str,
    expected_entry_id: str,
) -> dict[str, str]:
    tool = {
        "spec": {
            "name": TOOL_NAME,
            "description": "Return the deterministic Home Assistant OS acceptance marker.",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
        "function": {
            "type": "template",
            "value_template": TOOL_MARKER,
        },
        "enabled": True,
    }

    async with HAWebSocket(base_url, token) as ws:
        catalog = await ws.call(WS_COMMAND, action="agents")
        agents = catalog["agents"]
        assert len(agents) == 1, agents
        agent = agents[0]
        assert agent["entry_id"] == expected_entry_id
        entry_id = agent["entry_id"]
        subentry_id = agent["subentry_id"]
        states = await ws.call("get_states")
        conversation_agents = [
            state["entity_id"]
            for state in states
            if state["entity_id"].startswith(f"conversation.{DOMAIN}")
        ]
        assert len(conversation_agents) == 1, conversation_agents
        conversation_agent_id = conversation_agents[0]

        config = await ws.call(
            WS_COMMAND,
            action="get",
            section="configuration",
            entry_id=entry_id,
            subentry_id=subentry_id,
        )
        saved = await ws.call(
            WS_COMMAND,
            action="update",
            section="configuration",
            entry_id=entry_id,
            subentry_id=subentry_id,
            revision=config["revision"],
            config={
                "api_mode": "chat_completions",
                "chat_model": MODEL,
                "reasoning_effort": "none",
                "max_tokens": 128,
                # Keep this acceptance journey focused on Supervisor state
                # persistence; the stock prompt exercises optional template
                # helpers that have separate integration coverage.
                "prompt": "You are a concise assistant. Use the provided tools when asked.",
                "knowledge_enabled": True,
                "functions": [tool],
            },
        )
        assert saved["config"]["knowledge_enabled"] is True

        knowledge = await ws.call(
            WS_COMMAND,
            action="create",
            section="knowledge",
            entry_id=entry_id,
            subentry_id=subentry_id,
            title="HA OS retained reference",
            description="Supervisor VM acceptance marker",
            content=f"The HA OS retained marker is {KNOWLEDGE_MARKER}.",
            enabled=True,
        )
        source_id = knowledge["source"]["source_id"]

        result = await ws.call(
            "conversation/process",
            text=(
                f"Call the {TOOL_NAME} tool now. "
                "After it returns, include its exact marker in your final answer."
            ),
            language="en",
            agent_id=conversation_agent_id,
        )
        speech = result["response"]["speech"]["plain"]["speech"]
        assert TOOL_MARKER in speech

    assert len(DeterministicProvider.requests) >= 2
    assert TOOL_MARKER in json.dumps(
        DeterministicProvider.requests[-1]["body"], ensure_ascii=False
    )
    return {
        "entry_id": entry_id,
        "agent_id": conversation_agent_id,
        "subentry_id": subentry_id,
        "source_id": source_id,
    }


async def _verify_after_restart(
    base_url: str,
    token: str,
    identity: dict[str, str],
) -> None:
    async with HAWebSocket(base_url, token) as ws:
        catalog = await ws.call(WS_COMMAND, action="agents")
        agent = next(
            item
            for item in catalog["agents"]
            if item["entry_id"] == identity["entry_id"]
        )
        assert agent["subentry_id"] == identity["subentry_id"]

        source = await ws.call(
            WS_COMMAND,
            action="get",
            section="knowledge",
            entry_id=identity["entry_id"],
            subentry_id=identity["subentry_id"],
            source_id=identity["source_id"],
        )
        assert KNOWLEDGE_MARKER in source["source"]["content"]

        result = await ws.call(
            "conversation/process",
            text=f"Call the {TOOL_NAME} tool again after the Supervisor restart.",
            language="en",
            agent_id=identity["agent_id"],
        )
        assert TOOL_MARKER in result["response"]["speech"]["plain"]["speech"]


async def _mutate_knowledge_before_restore(
    base_url: str,
    token: str,
    identity: dict[str, str],
) -> None:
    async with HAWebSocket(base_url, token) as ws:
        changed = await ws.call(
            WS_COMMAND,
            action="update",
            section="knowledge",
            entry_id=identity["entry_id"],
            subentry_id=identity["subentry_id"],
            source_id=identity["source_id"],
            content="MUTATED_AFTER_SUPERVISOR_BACKUP",
        )
        assert "MUTATED_AFTER_SUPERVISOR_BACKUP" in changed["source"]["content"]


def _wait_supervisor_job(ssh: HostSSH, job_id: str, *, timeout: float = 900) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        try:
            response = _supervisor_api(ssh, "GET", f"/jobs/{job_id}", timeout=30)
        except AssertionError as err:
            # A full Supervisor restore deliberately stops Core. Its API
            # transport lives in that container, so resume polling once Core
            # returns; every other transport error remains a hard failure.
            message = str(err)
            core_unavailable = (
                "Error response from daemon: No such container: homeassistant" in message
                or (
                    "Error response from daemon: container " in message
                    and (" is not running" in message or " is restarting" in message)
                )
            )
            if not core_unavailable:
                raise
            time.sleep(5)
            continue
        assert response.get("result") == "ok", response
        last = response.get("data", {})
        if last.get("done"):
            assert not last.get("errors"), last
            return last
        time.sleep(5)
    raise TimeoutError(f"Supervisor job {job_id} did not finish: {last}")


def _wait_for_core_restart(ssh: HostSSH, previous_started: str, *, timeout: float = 600) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = ssh.run(
            "docker inspect -f '{{.State.StartedAt}}' homeassistant",
            check=False,
        )
        if result.returncode == 0:
            started = result.stdout.strip()
            if started and started != previous_started:
                return started
        time.sleep(3)
    raise TimeoutError("Supervisor did not restart Core")


def _restart_core_via_supervisor(ssh: HostSSH) -> None:
    """Request a Core restart from the host's Supervisor CLI."""
    deadline = time.monotonic() + 300
    while True:
        result = ssh.run("ha core restart", timeout=120, check=False)
        if result.returncode == 0:
            return
        output = f"{result.stdout}\n{result.stderr}"
        if (
            "Another job is running for job group home_assistant_core" not in output
            or time.monotonic() >= deadline
        ):
            raise AssertionError(
                f"Supervisor Core restart command failed:\n{output}"
            )
        time.sleep(5)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


async def _run(args: argparse.Namespace) -> None:
    base_url = f"http://127.0.0.1:{args.ha_port}"
    ssh = HostSSH(Path(args.ssh_key).resolve(), args.ssh_port)
    repo_root = Path(args.repo_root).resolve()
    evidence_path = Path(args.evidence).resolve()
    evidence_path.parent.mkdir(parents=True, exist_ok=True)

    _wait_ssh(ssh)
    _wait_http(base_url)

    os_info = ssh.json("ha os info --raw-json")
    supervisor_info = ssh.json("ha supervisor info --raw-json")
    core_info = ssh.json("ha core info --raw-json")
    assert os_info
    assert supervisor_info
    assert core_info

    installation = _json_request(f"{base_url}/api/onboarding/installation_type")
    assert installation["installation_type"] == "Home Assistant OS"

    token = _onboard(base_url)
    _install_candidate(ssh, repo_root, Path(args.work_dir).resolve())

    # Restart Core through Supervisor so Home Assistant discovers the newly
    # installed custom integration exactly as it would on an HA OS appliance.
    before_started = ssh.run(
        "docker inspect -f '{{.State.StartedAt}}' homeassistant"
    ).stdout.strip()
    _restart_core_via_supervisor(ssh)
    deadline = time.monotonic() + 600
    after_started = before_started
    while time.monotonic() < deadline:
        time.sleep(3)
        result = ssh.run(
            "docker inspect -f '{{.State.StartedAt}}' homeassistant",
            check=False,
        )
        if result.returncode == 0:
            after_started = result.stdout.strip()
            if after_started and after_started != before_started:
                break
    assert after_started != before_started, "Supervisor did not restart Core"
    _wait_http(base_url, token=token, timeout=600)

    async with _provider_server():
        entry_id = _create_eoai_entry(base_url, token)
        identity = await _configure_and_seed(base_url, token, entry_id)

        before_second = after_started
        _restart_core_via_supervisor(ssh)
        deadline = time.monotonic() + 600
        second_started = before_second
        while time.monotonic() < deadline:
            time.sleep(3)
            result = ssh.run(
                "docker inspect -f '{{.State.StartedAt}}' homeassistant",
                check=False,
            )
            if result.returncode == 0:
                second_started = result.stdout.strip()
                if second_started and second_started != before_second:
                    break
        assert second_started != before_second, "second Supervisor Core restart failed"
        _wait_http(base_url, token=token, timeout=600)
        await _verify_after_restart(base_url, token, identity)

        # Exercise the real appliance restore path: Supervisor creates a full
        # backup containing EOAI, live EOAI data is changed, then Supervisor
        # restores the archive and Core is restarted before state is checked.
        created = _supervisor_api(
            ssh,
            "POST",
            "/backups/new/full",
            {"name": "EOAI Supervisor restore acceptance", "compressed": True},
            timeout=900,
        )
        assert created.get("result") == "ok", created
        backup_slug = created.get("data", {}).get("slug")
        assert backup_slug, created
        backup_detail = _supervisor_api(
            ssh, "GET", f"/backups/{backup_slug}/info", timeout=60
        )
        assert backup_detail.get("result") == "ok", backup_detail
        backup_record = backup_detail.get("data", {})
        assert backup_record.get("type") == "full", backup_record
        inventory = _supervisor_api(ssh, "GET", "/backups/info", timeout=60)
        assert inventory.get("result") == "ok", inventory
        backup_summary = next(
            item
            for item in inventory.get("data", {}).get("backups", [])
            if item.get("slug") == backup_slug
        )
        assert backup_summary.get("content", {}).get("homeassistant") is True, backup_summary

        await _mutate_knowledge_before_restore(base_url, token, identity)
        restore = _supervisor_api(
            ssh,
            "POST",
            f"/backups/{backup_slug}/restore/full",
            {"background": True},
            timeout=60,
        )
        assert restore.get("result") == "ok", restore
        restore_job = restore.get("data", {}).get("job_id")
        assert restore_job, restore
        _wait_supervisor_job(ssh, restore_job, timeout=900)
        _wait_http(base_url, token=token, timeout=900)

        # A real Supervisor Core restart after restore rules out success based
        # only on the still-running process's in-memory state.
        before_restore_reboot = ssh.run(
            "docker inspect -f '{{.State.StartedAt}}' homeassistant"
        ).stdout.strip()
        _restart_core_via_supervisor(ssh)
        _wait_for_core_restart(ssh, before_restore_reboot, timeout=600)
        _wait_http(base_url, token=token, timeout=600)
        await _verify_after_restart(base_url, token, identity)

        deleted = _supervisor_api(
            ssh, "DELETE", f"/backups/{backup_slug}", timeout=120
        )
        assert deleted.get("result") == "ok", deleted

    evidence = {
        "candidate_sha": os.environ.get("GITHUB_SHA"),
        "installation_type": installation["installation_type"],
        "haos": os_info,
        "supervisor": supervisor_info,
        "core": core_info,
        "entry_id": identity["entry_id"],
        "subentry_id": identity["subentry_id"],
        "knowledge_source_id": identity["source_id"],
        "supervisor_core_restart": True,
        "supervisor_full_backup_restore_reboot": True,
        "provider_calls": len(DeterministicProvider.requests),
        "tool_marker": TOOL_MARKER,
        "knowledge_marker": KNOWLEDGE_MARKER,
        "candidate_manifest_sha256": _sha256(
            repo_root / "custom_components" / DOMAIN / "manifest.json"
        ),
    }
    evidence_path.write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--ssh-key", required=True)
    parser.add_argument("--ssh-port", type=int, default=22222)
    parser.add_argument("--ha-port", type=int, default=8123)
    parser.add_argument("--evidence", required=True)
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
