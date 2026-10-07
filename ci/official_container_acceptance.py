"""Exercise EOAI inside Home Assistant's published Container image."""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import platform
import shutil
import subprocess
import threading
import time
from typing import ClassVar
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

DOMAIN = "extended_openai_conversation_responses"
HTTP_PORT = 8123


class Provider(BaseHTTPRequestHandler):
    """Small deterministic provider reachable from the host-networked HA container."""

    requests: ClassVar[list[str]] = []

    def log_message(self, *args):
        del args

    def reply(self, status, value, content_type="application/json"):
        body = value.encode() if isinstance(value, str) else json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        type(self).requests.append(self.path)
        if self.path == "/v1/models":
            return self.reply(
                200,
                {
                    "object": "list",
                    "data": [
                        {
                            "id": "gpt-5.6",
                            "object": "model",
                            "created": 0,
                            "owned_by": "official-container-acceptance",
                        }
                    ],
                },
            )
        if self.path == "/fact":
            return self.reply(200, {"answer": "retained-fact"})
        if self.path == "/page":
            return self.reply(
                200,
                '<div id="answer">retained-fact</div>',
                "text/html",
            )
        return self.reply(404, {})

    def do_POST(self):
        type(self).requests.append(self.path)
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        text = (
            '{"count":0}'
            if "STRUCTURED_RUNTIME" in json.dumps(body)
            else "Isolated runtime healthy"
        )
        if self.path == "/v1/chat/completions":
            chunk = {
                "id": "official-container",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": "gpt-5.6",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant", "content": text},
                        "finish_reason": "stop",
                    }
                ],
            }
            return self.reply(
                200,
                "data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n",
                "text/event-stream",
            )
        if self.path == "/v1/responses":
            item = {
                "id": "msg-official-container",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [
                    {
                        "type": "output_text",
                        "text": text,
                        "annotations": [],
                        "logprobs": [],
                    }
                ],
            }
            response = {
                "id": "resp-official-container",
                "object": "response",
                "created_at": 0,
                "status": "completed",
                "error": None,
                "incomplete_details": None,
                "instructions": None,
                "model": "gpt-5.6",
                "output": [item],
                "parallel_tool_calls": True,
                "tools": [],
                "tool_choice": "auto",
                "usage": None,
            }
            events = [
                {
                    "type": "response.output_item.added",
                    "output_index": 0,
                    "item": {**item, "status": "in_progress", "content": []},
                    "sequence_number": 0,
                },
                {
                    "type": "response.output_text.delta",
                    "content_index": 0,
                    "delta": text,
                    "item_id": item["id"],
                    "output_index": 0,
                    "sequence_number": 1,
                },
                {
                    "type": "response.output_item.done",
                    "output_index": 0,
                    "item": item,
                    "sequence_number": 2,
                },
                {
                    "type": "response.completed",
                    "response": response,
                    "sequence_number": 3,
                },
            ]
            return self.reply(
                200,
                "".join("data: " + json.dumps(event) + "\n\n" for event in events),
                "text/event-stream",
            )
        return self.reply(404, {})


def _docker(*args, timeout=600, capture_output=True):
    return subprocess.run(
        ["docker", *args],
        text=True,
        capture_output=capture_output,
        timeout=timeout,
        check=False,
    )


def _run_driver(image: str, root: Path, phase: str, endpoint: str, evidence: Path):
    for name in ("result.json", "observed.json", "release", "recorder-pending.json"):
        (root / name).unlink(missing_ok=True)
    result = _docker(
        "run",
        "--rm",
        "--network",
        "host",
        "-e",
        "PYTHONUNBUFFERED=1",
        "-v",
        f"{root}:/config",
        image,
        "python",
        "/config/driver.py",
        "/config",
        phase,
        endpoint,
        "--healthy-provider",
    )
    (evidence / f"{phase}.log").write_text(
        result.stdout + "\n" + result.stderr, encoding="utf-8"
    )
    assert result.returncode == 0, (
        f"official HA container phase {phase!r} failed; "
        f"see {evidence / (phase + '.log')}"
    )
    observed = json.loads((root / "result.json").read_text(encoding="utf-8"))
    (evidence / f"{phase}.json").write_text(
        json.dumps(observed, indent=2), encoding="utf-8"
    )
    return observed


def _validate(first: dict, recovered: dict, expected_ha: str) -> None:
    required = {
        "conversation",
        "structured_ai_task",
        "rest",
        "scrape",
        "memory",
        "knowledge",
    }
    for proof in (first, recovered):
        assert proof["after"]["homeassistant"] == expected_ha
        assert required <= set(proof["paths"])
        assert proof["entities"] >= 2
        assert proof["controls"]["callbacks"] == 3
        before_pytest = {
            name: version
            for name, version in proof["before"].items()
            if name.startswith("pytest")
        }
        after_pytest = {
            name: version
            for name, version in proof["after"].items()
            if name.startswith("pytest")
        }
        assert after_pytest == before_pytest
    assert first["entry"] == recovered["entry"]
    assert first["entities"] == recovered["entities"]


def _normal_entrypoint(image: str, root: Path, evidence: Path) -> None:
    """Boot the populated config with the image's normal entrypoint and HTTP stack."""
    name = f"eoai-official-ha-{int(time.time())}"
    created = _docker(
        "run",
        "-d",
        "--name",
        name,
        "--network",
        "host",
        "-v",
        f"{root}:/config",
        image,
    )
    assert created.returncode == 0, created.stderr
    try:
        deadline = time.monotonic() + 180
        ready = False
        url = f"http://127.0.0.1:{HTTP_PORT}/api/"
        last_response = "No response"
        while time.monotonic() < deadline:
            state = _docker("inspect", "-f", "{{.State.Running}}", name)
            if state.returncode != 0 or state.stdout.strip() != "true":
                break
            try:
                with urlopen(url, timeout=2) as response:
                    last_response = f"HTTP {response.status}"
                    ready = response.status < 500
                    if ready:
                        break
            except HTTPError as error:
                last_response = f"HTTP {error.code}"
                # HA can legitimately answer an unauthenticated readiness request
                # with 401/403 once its HTTP stack is fully serving.
                if error.code in {401, 403}:
                    ready = True
                    break
            except (URLError, TimeoutError) as error:
                last_response = str(error)
            time.sleep(1)
        logs = _docker("logs", name)
        (evidence / "entrypoint.log").write_text(
            logs.stdout + "\n" + logs.stderr, encoding="utf-8"
        )
        (evidence / "entrypoint-readiness.json").write_text(
            json.dumps({"url": url, "ready": ready, "last_response": last_response}),
            encoding="utf-8",
        )
        assert ready, "official HA image did not become HTTP-ready via normal entrypoint"
        lowered = (logs.stdout + logs.stderr).lower()
        assert "setup failed for custom integration" not in lowered
        assert "unable to set up" not in lowered or DOMAIN not in lowered
    finally:
        _docker("stop", "-t", "30", name, timeout=60)
        _docker("rm", "-f", name, timeout=60)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--expected-ha-version", required=True)
    parser.add_argument("--expected-machine", required=True)
    parser.add_argument("--expected-image-arch", required=True)
    parser.add_argument("--component", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--candidate-sha", required=True)
    args = parser.parse_args()

    root = (args.work / "config").resolve()
    evidence = (args.work / "evidence").resolve()
    root.mkdir(parents=True)
    evidence.mkdir()
    shutil.copytree(args.component.resolve(), root / "custom_components" / DOMAIN)
    shutil.copyfile(Path(__file__).with_name("isolated_ha_runtime.py"), root / "driver.py")
    (root / "configuration.yaml").write_text(
        "homeassistant:\n"
        "  name: Official Container Acceptance\n"
        "recorder:\n"
        "api:\n"
        "http:\n"
        # Keep HA's stable default port: a custom YAML port is an unconfirmed
        # HTTP configuration trial that can revert during the recovery phase.
        f"  server_port: {HTTP_PORT}\n",
        encoding="utf-8",
    )

    version = _docker(
        "run",
        "--rm",
        args.image,
        "python",
        "-c",
        "from importlib.metadata import version; print(version('homeassistant'))",
    )
    assert version.returncode == 0, version.stderr
    assert version.stdout.strip() == args.expected_ha_version
    image_id = _docker("image", "inspect", "-f", "{{.Id}}", args.image)
    assert image_id.returncode == 0 and image_id.stdout.strip()
    image_arch = _docker("image", "inspect", "-f", "{{.Architecture}}", args.image)
    assert image_arch.returncode == 0, image_arch.stderr
    assert image_arch.stdout.strip() == args.expected_image_arch

    container_machine = _docker(
        "run",
        "--rm",
        args.image,
        "python",
        "-c",
        "import platform; print(platform.machine())",
    )
    assert container_machine.returncode == 0, container_machine.stderr
    assert container_machine.stdout.strip() == args.expected_machine
    assert platform.machine() == args.expected_machine

    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{server.server_port}"
    try:
        first = _run_driver(args.image, root, "seed", endpoint, evidence)
        recovered = _run_driver(args.image, root, "recover", endpoint, evidence)
        _validate(first, recovered, args.expected_ha_version)
        _normal_entrypoint(args.image, root, evidence)
        assert {"/v1/models", "/fact", "/page"} <= set(Provider.requests)
        assert {"/v1/chat/completions", "/v1/responses"} & set(Provider.requests)
        (evidence / "certification.json").write_text(
            json.dumps(
                {
                    "candidate_sha": args.candidate_sha,
                    "passed": True,
                    "homeassistant": args.expected_ha_version,
                    "official_container": True,
                    "image": args.image,
                    "image_id": image_id.stdout.strip(),
                    "machine": platform.machine(),
                    "container_machine": container_machine.stdout.strip(),
                    "image_architecture": image_arch.stdout.strip(),
                    "phases": ["seed", "recover", "entrypoint"],
                    "retained_entry": first["entry"] == recovered["entry"],
                    "retained_entities": first["entities"] == recovered["entities"],
                    "paths": recovered["paths"],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == "__main__":
    main()
