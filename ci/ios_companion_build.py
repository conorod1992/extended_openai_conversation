"""Identify and share compiled iOS test products, never acceptance results."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tarfile
import tempfile
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
        "schema": 1,
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
                ROOT / WORKFLOW,
                ROOT / "ci/patch_ios_companion_e2e.py",
                ROOT / "ci/prepare_ios_build_tools.sh",
                Path(__file__),
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
    return True


def package(ios: Path, identity: dict) -> None:
    bundle = ios / "eoai-native"
    (bundle / "identity.json").write_text(json.dumps(identity, sort_keys=True) + "\n")
    validate_products(ios, identity)
    with tarfile.open(ios / "native-products.tar", "w") as packed:
        packed.add(bundle / "identity.json", arcname="identity.json")
        packed.add(bundle / "Build/Products", arcname="Build/Products")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=["identity", "restore", "package", "validate"]
    )
    parser.add_argument("checkout", type=Path)
    args = parser.parse_args()
    identity = build_identity(args.checkout)
    if args.command != "identity" and os.environ.get(
        "IOS_BUILD_CACHE_ID"
    ) != identity_key(identity):
        raise ValueError("Native inputs changed after resolving the build identity")
    if args.command == "identity":
        key = identity_key(identity)
        # Dependencies do not change when only the UI journey changes.
        gems = digest_files([args.checkout / "Gemfile", args.checkout / "Gemfile.lock"])
        tools = digest_files(
            [
                path
                for path in (
                    args.checkout / "BuildTools/Package.swift",
                    args.checkout / "BuildTools/Package.resolved",
                    args.checkout / "Tools/build_tool",
                    ROOT / "ci/prepare_ios_build_tools.sh",
                )
                if path.is_file()
            ]
        )
        with open(os.environ["GITHUB_ENV"], "a") as stream:
            stream.write(f"IOS_BUILD_CACHE_ID={key}\nIOS_GEMS_CACHE_ID={gems}\n")
            stream.write(
                f"IOS_TOOLS_CACHE_ID={identity['xcode'].splitlines()[-1].replace(' ', '-')}-{identity['architecture']}-{tools}\n"
            )
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
