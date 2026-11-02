"""Execute the actual host-tool preparation script with controlled tool failures."""

import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "generation_failures,lint_fails,expected_attempts,success",
    [
        (0, False, 1, True),
        (1, False, 2, True),
        (99, False, 3, False),
        (0, True, 3, False),
    ],
)
def test_preparation_recovers_only_within_its_bounded_host_tool_attempts(
    tmp_path, generation_failures, lint_fails, expected_attempts, success
):
    checkout = tmp_path / "pinned iOS checkout"
    tools = checkout / "Tools"
    tools.mkdir(parents=True)
    wrapper = tools / "build_tool"
    wrapper.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s:%s\n' "$*" "${SDKROOT-unset}" >> "$TRACE"
if [[ "$1" = swiftgen ]]; then
  count=0
  if [[ -f "$COUNTER" ]]; then count=$(cat "$COUNTER"); fi
  count=$((count + 1))
  echo "$count" > "$COUNTER"
  if [[ "$count" -le "$GENERATION_FAILURES" ]]; then exit 23; fi
elif [[ "$LINT_FAILS" = 1 ]]; then
  exit 24
fi
""",
        encoding="utf-8",
        newline="\n",
    )
    wrapper.chmod(0o755)
    trace, counter, sleeps = (
        tmp_path / name for name in ("trace", "counter", "sleeps")
    )
    bash = "C:/Program Files/Git/bin/bash.exe" if os.name == "nt" else "bash"
    result = subprocess.run(
        [
            bash,
            "-c",
            'sleep() { printf "%s\\n" "$*" >> "$SLEEPS"; }; '
            'export -f sleep; exec bash "$@"',
            "prepare-ios-build-tools",
            (ROOT / "ci/prepare_ios_build_tools.sh").as_posix(),
            checkout.as_posix(),
        ],
        env={
            **os.environ,
            "SDKROOT": "must-not-use-the-simulator-sdk-for-host-tools",
            "TRACE": trace.as_posix(),
            "COUNTER": counter.as_posix(),
            "SLEEPS": sleeps.as_posix(),
            "GENERATION_FAILURES": str(generation_failures),
            "LINT_FAILS": "1" if lint_fails else "0",
        },
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == (0 if success else 1), result.stderr
    assert int(counter.read_text()) == expected_attempts
    commands = trace.read_text().splitlines()
    assert commands.count("swiftgen --version:unset") == expected_attempts
    assert commands.count("swiftlint version:unset") == (
        expected_attempts if lint_fails else int(success)
    )
    assert all(command.endswith(":unset") for command in commands)
    assert (sleeps.read_text().splitlines() if sleeps.exists() else []) == [
        str(attempt * 5) for attempt in range(1, expected_attempts)
    ]
    if not success:
        assert "failed after 3 attempts" in result.stderr
