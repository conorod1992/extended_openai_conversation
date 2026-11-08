"""Identify and share compiled iOS test products, never acceptance results."""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
from urllib.parse import urlencode
import zipfile

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ".github/workflows/ios-companion-app.yml"


def digest_files(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.name.encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def build_identity(ios: Path) -> dict:
    """Everything capable of changing the native binaries or their load paths."""
    return {
        "schema": 2,
        "ios_source_sha": os.environ["IOS_SOURCE_SHA"],
        "architecture": platform.machine(),
        "xcode": subprocess.check_output(["xcodebuild", "-version"], text=True).strip(),
        "sdk": subprocess.check_output(
            ["xcrun", "--sdk", "iphonesimulator", "--show-sdk-version"], text=True
        ).strip(),
        "checkout": str(ios.resolve()),
        "configuration": "Debug",
        "scheme": "Tests-UI",
        "inputs": digest_files(
            [
                ROOT / "ci/patch_ios_companion_e2e.py",
                *[
                    ios / name
                    for name in subprocess.check_output(
                        ["git", "-C", str(ios), "ls-files", "*Package.resolved"],
                        text=True,
                    ).splitlines()
                ],
            ]
        ),
    }


def identity_key(identity: dict) -> str:
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


def cache_identities(ios: Path, identity: dict) -> dict[str, str]:
    """Incremental state is rebuilt; unlike executable artifacts it may fall back."""
    compatibility = identity_key(
        {
            key: identity[key]
            for key in (
                "xcode",
                "sdk",
                "architecture",
                "checkout",
                "configuration",
                "scheme",
            )
        }
    )
    locks = [
        ios / name
        for name in subprocess.check_output(
            ["git", "-C", str(ios), "ls-files", "*Package.resolved"], text=True
        ).splitlines()
    ]
    tools = [
        path
        for path in (
            ios / "BuildTools/Package.swift",
            ios / "BuildTools/Package.resolved",
            ios / "Tools/build_tool",
            ROOT / "ci/prepare_ios_build_tools.sh",
        )
        if path.is_file()
    ]
    return {
        "IOS_BUILD_CACHE_ID": identity_key(identity),
        "IOS_CACHE_COMPATIBILITY": compatibility,
        "IOS_INCREMENTAL_CACHE_ID": f"{compatibility}-{identity['ios_source_sha']}-{digest_files(locks)}",
        "IOS_PACKAGES_CACHE_ID": digest_files(locks),
        "IOS_TOOLS_CACHE_ID": f"{compatibility}-{digest_files(tools)}",
    }


def gh_json(endpoint: str) -> dict:
    return json.loads(subprocess.check_output(["gh", "api", endpoint], text=True))


def find_artifact(repository: str, name: str) -> dict | None:
    # Artifacts are repository-scoped, unlike branch-scoped Actions caches.
    # Only accept our own workflow's non-PR builds, with the exact native key.
    query = urlencode({"name": name, "per_page": 100})
    for page in range(1, 11):
        artifacts = gh_json(
            f"repos/{repository}/actions/artifacts?{query}&page={page}"
        )["artifacts"]
        for artifact in artifacts:
            if artifact["expired"] or artifact["name"] != name:
                continue
            run = gh_json(
                f"repos/{repository}/actions/runs/{artifact['workflow_run']['id']}"
            )
            if (
                run["path"] == WORKFLOW
                and run["event"] in {"push", "schedule", "workflow_dispatch"}
                and run["head_repository"]["full_name"] == repository
            ):
                return artifact
        if len(artifacts) < 100:
            break
    return None


def validate_products(ios: Path, expected: dict) -> None:
    bundle = ios / "eoai-native"
    actual = json.loads((bundle / "identity.json").read_text())
    if actual != expected:
        raise ValueError("Compiled iOS identity does not match this run")
    products = bundle / "Build/Products"
    if not list(products.glob("*.xctestrun")):
        raise ValueError("Compiled iOS bundle has no xctestrun file")
    if not list(products.rglob("*.xctest")):
        raise ValueError("Compiled iOS bundle has no XCTest bundle")
    if not list(products.rglob("*.app")):
        raise ValueError("Compiled iOS bundle has no simulator app")


def restore(ios: Path, identity: dict) -> bool:
    started = time.monotonic()
    repository = os.environ["GITHUB_REPOSITORY"]
    name = f"ios-native-{identity_key(identity)}"
    try:
        artifact = find_artifact(repository, name)
    except subprocess.CalledProcessError:
        # An unavailable artifact service must not suppress the cold build/test.
        print(
            "::warning::Native artifact lookup unavailable; building from pinned source"
        )
        return False
    if artifact is None:
        print("No matching native artifact; building from pinned source")
        return False
    with tempfile.TemporaryDirectory() as scratch:
        archive = Path(scratch) / "artifact.zip"
        with archive.open("wb") as stream:
            subprocess.run(
                [
                    "gh",
                    "api",
                    f"repos/{repository}/actions/artifacts/{artifact['id']}/zip",
                ],
                stdout=stream,
                check=True,
            )
        with zipfile.ZipFile(archive) as zipped:
            # Keep executable modes and framework symlinks inside a tar archive.
            native_tar = Path(scratch) / "native-products.tar"
            with (
                zipped.open("native-products.tar") as source,
                native_tar.open("wb") as target,
            ):
                shutil.copyfileobj(source, target)
        with tarfile.open(native_tar) as packed:
            packed.extractall(ios / "eoai-native", filter="data")
    validate_products(ios, identity)
    with open(os.environ["GITHUB_ENV"], "a") as stream:
        stream.write(f"IOS_NATIVE_ARTIFACT_ID={artifact['id']}\n")
    print(
        f"Restored compiled iOS artifact {artifact['id']} from run {artifact['workflow_run']['id']}"
    )
    print(f"Native artifact restore took {time.monotonic() - started:.1f}s")
    return True


def package(ios: Path, identity: dict) -> None:
    bundle = ios / "eoai-native"
    (bundle / "identity.json").write_text(json.dumps(identity, sort_keys=True) + "\n")
    validate_products(ios, identity)
    with tarfile.open(ios / "native-products.tar", "w") as packed:
        packed.add(bundle / "identity.json", arcname="identity.json")
        packed.add(bundle / "Build/Products", arcname="Build/Products")
    print(
        f"Native product archive: {(ios / 'native-products.tar').stat().st_size / 1024**2:.1f} MiB"
    )


def prune_artifacts(repository: str, keep: int = 2) -> None:
    """Retain two reproducible native builds, never acceptance evidence."""
    candidates = []
    for page in range(1, 11):
        artifacts = gh_json(
            f"repos/{repository}/actions/artifacts?per_page=100&page={page}"
        )["artifacts"]
        candidates.extend(
            artifact
            for artifact in artifacts
            if re.fullmatch(r"ios-native-[a-f0-9]{64}", artifact["name"])
            and not artifact["expired"]
        )
        if len(artifacts) < 100:
            break
    candidates.sort(key=lambda artifact: artifact["created_at"], reverse=True)
    for artifact in candidates[keep:]:
        run = gh_json(
            f"repos/{repository}/actions/runs/{artifact['workflow_run']['id']}"
        )
        if run["path"] != WORKFLOW or run["head_repository"]["full_name"] != repository:
            continue
        subprocess.run(
            [
                "gh",
                "api",
                "--method",
                "DELETE",
                f"repos/{repository}/actions/artifacts/{artifact['id']}",
            ],
            check=True,
        )


def report_timings(repository: str, run_id: str) -> None:
    jobs = gh_json(
        f"repos/{repository}/actions/runs/{run_id}/jobs?filter=latest&per_page=100"
    )["jobs"]
    rows = []
    for job in jobs:
        for step in job.get("steps", []):
            if step.get("started_at") and step.get("completed_at"):
                elapsed = (
                    datetime.fromisoformat(step["completed_at"])
                    - datetime.fromisoformat(step["started_at"])
                ).total_seconds()
                rows.append(
                    {
                        "step": step["name"],
                        "seconds": elapsed,
                        "conclusion": step["conclusion"],
                    }
                )
    Path("ios-companion-timings.json").write_text(json.dumps(rows, indent=2) + "\n")
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as stream:
        stream.write("\n| Step | Seconds | Result |\n|---|---:|---|\n")
        for row in rows:
            stream.write(
                f"| {row['step']} | {row['seconds']:.0f} | {row['conclusion']} |\n"
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=["identity", "restore", "package", "validate", "prune", "report"],
    )
    parser.add_argument("checkout", type=Path)
    args = parser.parse_args()
    if args.command == "prune":
        prune_artifacts(os.environ["GITHUB_REPOSITORY"])
        return
    if args.command == "report":
        report_timings(os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_RUN_ID"])
        return
    identity = build_identity(args.checkout)
    if args.command != "identity" and os.environ.get(
        "IOS_BUILD_CACHE_ID"
    ) != identity_key(identity):
        raise ValueError("Native inputs changed after resolving the build identity")
    if args.command == "identity":
        key = identity_key(identity)
        # Dependencies do not change when only the UI journey changes.
        with open(os.environ["GITHUB_ENV"], "a") as stream:
            for name, value in cache_identities(args.checkout, identity).items():
                stream.write(f"{name}={value}\n")
        print(f"Native identity: {key}")
    elif args.command == "restore":
        hit = restore(args.checkout, identity)
        with open(os.environ["GITHUB_OUTPUT"], "a") as stream:
            stream.write(f"hit={str(hit).lower()}\n")
    elif args.command == "package":
        package(args.checkout, identity)
    else:
        validate_products(args.checkout, identity)


if __name__ == "__main__":
    main()
