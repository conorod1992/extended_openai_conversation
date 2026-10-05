"""Control a disposable, independently provisioned HA interpreter externally."""

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import platform
import shutil
import sqlite3
import subprocess
import threading
import time
from typing import ClassVar


def validate_evidence(first, recovered):
    """A healthy child exit alone cannot certify isolation or retained recovery."""
    assert not {"openai", "voluptuous-openapi", "beautifulsoup4", "lxml"} & set(
        first["before"]
    ), "Optional integration packages were preinstalled"
    required = {
        "conversation",
        "structured_ai_task",
        "rest",
        "scrape",
        "memory",
        "knowledge",
    }
    for proof in (first, recovered):
        assert required <= set(proof["paths"]), "Missing delayed-import execution"
        assert not any(name.startswith("pytest") for name in proof["before"]), (
            "Contaminated runtime"
        )
        assert "homeassistant" in proof["before"]
        assert proof["entities"] >= 2
    assert first["entry"] == recovered["entry"], (
        "Recovery replaced persisted installation"
    )
    assert first["entities"] == recovered["entities"], (
        "Duplicate registrations after restart"
    )


class Provider(BaseHTTPRequestHandler):
    mode = "healthy"
    requests: ClassVar[list[str]] = []

    def log_message(self, *args):
        pass

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
            if self.mode != "healthy":
                return self.reply(
                    401 if self.mode == "auth" else 503,
                    {
                        "error": {
                            "message": "controlled startup",
                            "type": "invalid_api_key",
                        }
                    },
                )
            return self.reply(
                200,
                {
                    "object": "list",
                    "data": [
                        {
                            "id": "gpt-5.6",
                            "object": "model",
                            "created": 0,
                            "owned_by": "acceptance",
                        }
                    ],
                },
            )
        if self.path == "/fact":
            return self.reply(200, {"answer": "retained-fact"})
        if self.path == "/page":
            return self.reply(200, '<div id="answer">retained-fact</div>', "text/html")
        self.reply(404, {})

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
                "id": "isolated",
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
            self.reply(
                200,
                "data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n",
                "text/event-stream",
            )
        elif self.path == "/v1/responses":
            item = {
                "id": "msg-isolated",
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
                "id": "resp-isolated",
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
            self.reply(
                200,
                "".join("data: " + json.dumps(event) + "\n\n" for event in events),
                "text/event-stream",
            )
        else:
            self.reply(404, {})


def run(runtime, root, phase, endpoint, evidence):
    for name in ("observed.json", "release", "result.json", "recorder-pending.json"):
        (root / name).unlink(missing_ok=True)
    database = None
    order = []
    if phase in ("recover-recorder-first", "recover-provider-first"):
        database = sqlite3.connect(root / "home-assistant_v2.db")
        database.execute("BEGIN EXCLUSIVE")
    with (evidence / (phase + ".log")).open("w") as log:
        child = subprocess.Popen(
            [runtime, "-I", str(root / "driver.py"), str(root), phase, endpoint],
            cwd=root,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 480
            while child.poll() is None:
                if time.monotonic() > deadline:
                    raise TimeoutError(
                        f"isolated {phase} did not settle; see {log.name}"
                    )
                if database is not None and (root / "recorder-pending.json").exists():
                    order.append("recorder-blocked")
                    pending = json.loads((root / "recorder-pending.json").read_text())
                    assert pending == {
                        "worker_alive": True,
                        "engine_created": True,
                        "ready": False,
                    }
                    if phase == "recover-provider-first":
                        Provider.mode = "healthy"
                        order.append("provider-restored")
                    database.rollback()
                    database.close()
                    database = None
                    order.append("recorder-released")
                if phase != "seed" and (root / "observed.json").exists():
                    Provider.mode = "healthy"
                    if "provider-restored" not in order:
                        order.append("provider-restored")
                    (root / "release").touch()
                time.sleep(0.05)
            assert child.returncode == 0, f"isolated {phase} failed: see {log.name}"
        finally:
            if child.poll() is None:
                child.kill()
            child.wait()
            if database is not None:
                database.rollback()
                database.close()
    source = root / "result.json"
    result = json.loads(source.read_text())
    result["readiness_order"] = order
    if phase in ("recover-recorder-first", "recover-provider-first"):
        assert order[0] == "recorder-blocked", "Recorder stall was not exercised"
    (evidence / (phase + ".json")).write_text(json.dumps(result, indent=2))
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-python", required=True)
    parser.add_argument("--component", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    args = parser.parse_args()
    root = args.work.resolve() / "config"
    evidence = args.work.resolve() / "evidence"
    root.mkdir(parents=True)
    evidence.mkdir()
    shutil.copytree(
        args.component,
        root / "custom_components" / "extended_openai_conversation_responses",
    )
    shutil.copyfile(
        Path(__file__).with_name("isolated_ha_runtime.py"), root / "driver.py"
    )
    (root / "configuration.yaml").write_text(
        "homeassistant:\n  name: Isolated acceptance\nrecorder:\n"
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{server.server_port}"
    try:
        first = run(args.runtime_python, root, "seed", endpoint, evidence)
        Provider.mode = "offline"
        second = run(args.runtime_python, root, "recover", endpoint, evidence)
        validate_evidence(first, second)
        for phase in ("recover-recorder-first", "recover-provider-first"):
            Provider.mode = "offline"
            recovered = run(args.runtime_python, root, phase, endpoint, evidence)
            validate_evidence(first, recovered)
        Provider.mode = "auth"
        run(args.runtime_python, root, "auth", endpoint, evidence)
        assert {"/v1/models", "/fact", "/page"} <= set(Provider.requests)
        assert {"/v1/chat/completions", "/v1/responses"} & set(Provider.requests)
        (evidence / "requests.json").write_text(json.dumps(Provider.requests))
        (evidence / "certification.json").write_text(
            json.dumps(
                {
                    "candidate_sha": subprocess.check_output(
                        ["git", "rev-parse", "HEAD"], text=True
                    ).strip(),
                    "passed": True,
                    "homeassistant": second["after"]["homeassistant"],
                    "runtime_python": args.runtime_python,
                    "cold_start_recovery": True,
                    "authentication_failure_requires_reauth": True,
                    "machine": platform.machine(),
                    "phases": [
                        "seed",
                        "recover",
                        "recover-recorder-first",
                        "recover-provider-first",
                        "auth",
                    ],
                },
                indent=2,
            )
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == "__main__":
    main()
