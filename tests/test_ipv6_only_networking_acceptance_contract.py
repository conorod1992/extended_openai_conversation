"""Contracts for the strict IPv6-only acceptance lane."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ipv6-only-networking-acceptance.yml"
JOURNEY = ROOT / "tests_real_ha" / "test_ipv6_only_networking_acceptance.py"


def test_ipv6_workflow_is_nightly_manual_only() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    trigger_block = text.split("concurrency:", 1)[0]

    assert "schedule:" in trigger_block
    assert "workflow_dispatch:" in trigger_block
    assert "pull_request:" not in trigger_block
    assert "push:" not in trigger_block
    assert 'RUN_IPV6_ONLY_ACCEPTANCE: "1"' in text
    assert 'NO_PROXY: "::1,[::1],127.0.0.1,localhost"' in text


def test_ipv6_journey_cannot_fall_back_to_ipv4() -> None:
    text = JOURNEY.read_text(encoding="utf-8")

    assert "socket.AF_INET6" in text
    assert 'sock.bind(("::1", 0))' in text
    assert 'socket.AF_INET, socket.SOCK_STREAM' in text
    assert 'sock.connect_ex(("127.0.0.1", port)) != 0' in text
    assert 'f"http://[::1]:{port}/v1"' in text
    assert 'f"http://[::1]:{port}/fact"' in text


def test_ipv6_journey_covers_provider_stream_rest_and_recovery() -> None:
    text = JOURNEY.read_text(encoding="utf-8")

    assert "text/event-stream" in text
    assert "await response.write(" in text
    assert '"type": "rest"' in text
    assert "_MARKER in json.dumps(endpoint.provider_requests[1][\"body\"])" in text
    assert "provider_failures_remaining = 3" in text
    assert "provider_count_after_failure" in text
    assert "recovered.response.error_code is None" in text
