#!/usr/bin/env python3
"""Validate HA-dev dependencies while allowing the intentional pytest-HA pin mismatch."""

from __future__ import annotations

import subprocess
import sys

result = subprocess.run(
    [sys.executable, "-m", "pip", "check"],
    check=False,
    capture_output=True,
    text=True,
)
lines = [
    line.strip()
    for line in (result.stdout + "\n" + result.stderr).splitlines()
    if line.strip()
]

allowed_prefix = "pytest-homeassistant-custom-component "
allowed_fragment = " has requirement homeassistant=="
unexpected = [
    line
    for line in lines
    if not (line.startswith(allowed_prefix) and allowed_fragment in line)
]

if unexpected:
    print("\n".join(unexpected), file=sys.stderr)
    raise SystemExit(result.returncode or 1)

for line in lines:
    if line.startswith(allowed_prefix) and allowed_fragment in line:
        print(f"Allowed HA-dev compatibility mismatch: {line}")

if result.returncode == 0:
    print("No dependency conflicts found.")
