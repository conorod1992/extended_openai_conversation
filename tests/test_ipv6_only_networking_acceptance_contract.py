"""Contracts for the strict IPv6-only acceptance lane."""

import ast
import json
from pathlib import Path

import httpx
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ipv6-only-networking-acceptance.yml"
JOURNEY = ROOT / "tests_real_ha" / "test_ipv6_only_networking_acceptance.py"


def test_ipv6_workflow_environment_keys_are_unique_ignoring_case() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    environments = [workflow.get("env", {})]
    for job in workflow["jobs"].values():
        environments.append(job.get("env", {}))
        environments.extend(step.get("env", {}) for step in job["steps"])

    for environment in environments:
        keys = [key.casefold() for key in environment]
        assert len(keys) == len(set(keys)), "Actions env keys ignore case"


def test_ipv6_workflow_is_weekly_manual_and_focused_pr() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    trigger_block = text.split("concurrency:", 1)[0]

    assert "schedule:" in trigger_block
    assert "workflow_dispatch:" in trigger_block
    assert "workflow_call:" in trigger_block
    policy = json.loads((ROOT / "ci/pr_workflows.json").read_text(encoding="utf-8"))
    paths = policy["ipv6-only-networking-acceptance"]["paths"]
    assert "ci/specialist_evidence.py" in paths
    assert "push:" not in trigger_block
    assert 'RUN_IPV6_ONLY_ACCEPTANCE: "1"' in text
    assert 'NO_PROXY: "::1,127.0.0.1,localhost"' in text


def test_ipv6_workflow_proxy_bypass_is_accepted_by_httpx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    environment = workflow["jobs"]["ipv6-only"]["env"]
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.setenv("NO_PROXY", environment["NO_PROXY"])

    # Construction parses bypass hosts even when no HTTP request is made.
    with httpx.Client(trust_env=True):
        pass


def test_ipv6_journey_cannot_fall_back_to_ipv4() -> None:
    text = JOURNEY.read_text(encoding="utf-8")

    assert "socket.AF_INET6" in text
    assert 'sock.bind(("::1", 0))' in text
    assert "socket.AF_INET, socket.SOCK_STREAM" in text
    assert 'sock.connect_ex(("127.0.0.1", port)) != 0' in text
    assert 'f"http://[::1]:{port}/v1"' in text
    assert 'f"http://[::1]:{port}/fact"' in text


def test_ipv6_journey_permits_only_loopback_socket_connections() -> None:
    module = ast.parse(JOURNEY.read_text(encoding="utf-8"))
    journey = next(
        node
        for node in module.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "test_ipv6_only_provider_stream_rest_tool_and_failure_recovery"
    )
    assert "socket_enabled" in {argument.arg for argument in journey.args.args}
    allowlist = next(
        node
        for node in ast.walk(journey)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "socket_allow_hosts"
    )
    assert set(ast.literal_eval(allowlist.args[0])) == {"::1", "127.0.0.1"}


def test_ipv6_journey_covers_provider_stream_rest_and_recovery() -> None:
    text = JOURNEY.read_text(encoding="utf-8")

    assert "text/event-stream" in text
    assert "await response.write(" in text
    assert '"type": "rest"' in text
    assert '_MARKER in json.dumps(endpoint.provider_requests[1]["body"])' in text
    assert "provider_failures_remaining = 3" in text
    assert "provider_count_after_failure" in text
    assert "recovered.response.error_code is None" in text
