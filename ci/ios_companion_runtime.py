"""Start fresh simulator/HA services early and gate testing on readiness."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
from urllib.error import URLError
from urllib.request import urlopen


def python_cache_key(root: Path) -> str:
    """Read only the two dependency inputs, without a workspace-wide glob."""
    digest = hashlib.sha256()
    for name in (
        "custom_components/extended_openai_conversation_responses/manifest.json",
        "ci/install_ha_dependencies.py",
    ):
        digest.update(name.encode() + b"\0" + (root / name).read_bytes() + b"\0")
    return digest.hexdigest()


def newest_phone(devices: dict) -> str:
    available = [
        (tuple(map(int, re.findall(r"\d+", runtime))), device["udid"])
        for runtime, entries in devices["devices"].items()
        if "iOS" in runtime
        for device in entries
        if device["name"] == "iPhone 17" and device.get("isAvailable", True)
    ]
    if not available:
        raise RuntimeError("No available iPhone 17 iOS simulator")
    return max(available)[1]


def start_simulator() -> None:
    devices = json.loads(
        subprocess.check_output(
            ["xcrun", "simctl", "list", "devices", "available", "--json"], text=True
        )
    )
    udid = newest_phone(devices)
    subprocess.run(["xcrun", "simctl", "shutdown", udid], check=False)
    subprocess.run(["xcrun", "simctl", "erase", udid], check=True)
    subprocess.run(["xcrun", "simctl", "boot", udid], check=True, timeout=60)
    log = Path(os.environ["RUNNER_TEMP"]) / "ios-simulator-boot.log"
    with log.open("wb") as stream:
        subprocess.Popen(
            ["xcrun", "simctl", "bootstatus", udid, "-b"],
            stdin=subprocess.DEVNULL,
            stdout=stream,
            stderr=subprocess.STDOUT,
        )
    with open(os.environ["GITHUB_ENV"], "a") as stream:
        stream.write(f"IOS_SIMULATOR_UDID={udid}\n")


def wait_for_ha(directory: Path, timeout: float = 180) -> None:
    deadline = time.monotonic() + timeout
    pid = int((directory / "companion-ha.pid").read_text())
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError as error:
            raise RuntimeError(
                "Disposable Home Assistant exited before testing"
            ) from error
        if (directory / "companion-ready.json").is_file():
            try:
                with urlopen("http://127.0.0.1:8123/", timeout=5) as response:
                    if response.status == 200:
                        return
            except URLError, TimeoutError:
                pass
        time.sleep(1)
    raise TimeoutError("Disposable Home Assistant did not become ready")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=["simulator-start", "ready", "python-cache-key"]
    )
    args = parser.parse_args()
    started = time.monotonic()
    if args.command == "python-cache-key":
        key = python_cache_key(Path(__file__).resolve().parents[1])
        with open(os.environ["GITHUB_ENV"], "a") as stream:
            stream.write(f"IOS_PIP_CACHE_ID={key}\n")
    elif args.command == "simulator-start":
        start_simulator()
    else:
        subprocess.run(
            ["xcrun", "simctl", "bootstatus", os.environ["IOS_SIMULATOR_UDID"], "-b"],
            check=True,
            timeout=300,
        )
        wait_for_ha(Path(os.environ["RUNNER_TEMP"]))
    print(f"Runtime {args.command} took {time.monotonic() - started:.1f}s")


if __name__ == "__main__":
    main()
