"""Compiled test products must remain portable and bound to exact native inputs."""

import io
import json
import subprocess
import tarfile
import zipfile

import pytest

from ci import ios_companion_build as native


def products(checkout, identity):
    bundle = checkout / "eoai-native"
    output = bundle / "Build/Products"
    output.mkdir(parents=True)
    (output / "Tests-UI.xctestrun").write_text("test plan")
    (output / "Debug-iphonesimulator/Runner.app/PlugIns/Tests.xctest").mkdir(
        parents=True
    )
    (output / "Debug-iphonesimulator/Runner.app/Runner").write_bytes(b"executable")
    (bundle / "identity.json").write_text(json.dumps(identity))
    return bundle


def test_roundtrip_reuses_products_but_not_results_or_intermediates(
    tmp_path, monkeypatch
):
    identity = {"schema": 1, "ios_source_sha": "pinned", "sdk": "27.0"}
    source, destination = tmp_path / "source", tmp_path / "destination"
    bundle = products(source, identity)
    (bundle / "Logs/Test").mkdir(parents=True)
    (bundle / "Logs/Test/old.xcresult").write_text("must not be reused")
    (bundle / "Build/Intermediates.noindex").mkdir()
    native.package(source, identity)
    artifact_zip = tmp_path / "artifact.zip"
    with zipfile.ZipFile(artifact_zip, "w") as zipped:
        zipped.write(source / "native-products.tar", "native-products.tar")
    with tarfile.open(source / "native-products.tar") as packed:
        assert all(
            not member.name.startswith(("Logs", "Build/Intermediates"))
            for member in packed
        )

    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setenv("GITHUB_ENV", str(tmp_path / "env"))
    monkeypatch.setattr(
        native, "find_artifact", lambda *_: {"id": 123, "workflow_run": {"id": 456}}
    )

    def download(command, *, stdout, check):
        assert command[-1] == "repos/owner/repo/actions/artifacts/123/zip"
        assert check
        stdout.write(artifact_zip.read_bytes())

    monkeypatch.setattr(native.subprocess, "run", download)
    assert native.restore(destination, identity)
    assert (
        destination
        / "eoai-native/Build/Products/Debug-iphonesimulator/Runner.app/Runner"
    ).read_bytes() == b"executable"
    assert not (destination / "eoai-native/Logs").exists()
    assert "IOS_NATIVE_ARTIFACT_ID=123" in (tmp_path / "env").read_text()


def test_identity_mismatch_or_missing_test_products_cannot_pass(tmp_path):
    identity = {"sdk": "27.0"}
    products(tmp_path, identity)
    with pytest.raises(ValueError, match="identity"):
        native.validate_products(tmp_path, {"sdk": "27.1"})
    (tmp_path / "eoai-native/Build/Products/Tests-UI.xctestrun").unlink()
    with pytest.raises(ValueError, match="xctestrun"):
        native.package(tmp_path, identity)


def test_lookup_skips_expired_wrong_workflow_and_pr_artifacts(monkeypatch):
    artifacts = [
        {
            "id": i,
            "name": "ios-native-key",
            "expired": i == 0,
            "workflow_run": {"id": i},
        }
        for i in range(4)
    ]

    def api(endpoint):
        if "artifacts?" in endpoint:
            return {"artifacts": artifacts}
        run_id = int(endpoint.rsplit("/", 1)[1])
        return {
            "path": "other.yml" if run_id == 1 else native.WORKFLOW,
            "event": "pull_request" if run_id == 2 else "workflow_dispatch",
            "head_repository": {"full_name": "owner/repo"},
        }

    monkeypatch.setattr(native, "gh_json", api)
    assert native.find_artifact("owner/repo", "ios-native-key")["id"] == 3


def test_lookup_failure_falls_back_to_a_cold_build(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")

    def unavailable(*_):
        raise subprocess.CalledProcessError(1, "gh")

    monkeypatch.setattr(native, "find_artifact", unavailable)
    assert not native.restore(tmp_path, {"sdk": "27"})


def test_artifact_required_retry_never_falls_back_to_compilation(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setenv("IOS_REQUIRE_NATIVE_ARTIFACT", "true")
    monkeypatch.setattr(native, "find_artifact", lambda *_: None)
    with pytest.raises(RuntimeError, match="refusing a cold build"):
        native.restore(tmp_path, {})

    def unavailable(*_):
        raise subprocess.CalledProcessError(1, "gh")

    monkeypatch.setattr(native, "find_artifact", unavailable)
    with pytest.raises(subprocess.CalledProcessError):
        native.restore(tmp_path, {})


def test_restore_rejects_tar_path_escape(tmp_path, monkeypatch):
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w") as packed:
        member = tarfile.TarInfo("../../escaped")
        member.size = 3
        packed.addfile(member, io.BytesIO(b"bad"))
    zipped_bytes = io.BytesIO()
    with zipfile.ZipFile(zipped_bytes, "w") as zipped:
        zipped.writestr("native-products.tar", archive.getvalue())
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setattr(native, "find_artifact", lambda *_: {"id": 1})

    def download(_, *, stdout, check):
        stdout.write(zipped_bytes.getvalue())

    monkeypatch.setattr(native.subprocess, "run", download)
    with pytest.raises(tarfile.OutsideDestinationError):
        native.restore(tmp_path / "ios", {})
    assert not (tmp_path / "escaped").exists()


def test_native_identity_changes_with_toolchain_and_patch_not_candidate(
    tmp_path, monkeypatch
):
    root = tmp_path / "repo"
    ios = tmp_path / "ios"
    ios.mkdir()
    monkeypatch.setattr(native, "ROOT", root)
    monkeypatch.setattr(native, "__file__", str(root / "ci/ios_companion_build.py"))
    for name in (
        native.WORKFLOW,
        "ci/patch_ios_companion_e2e.py",
        "ci/prepare_ios_build_tools.sh",
        "ci/ios_companion_build.py",
    ):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("original")
    monkeypatch.setenv("IOS_SOURCE_SHA", "pinned-ios")
    monkeypatch.setenv("GITHUB_SHA", "candidate-a")
    xcode = "Xcode 27.0\nBuild version 27A1"

    def command(args, **_):
        return "" if args[0] == "git" else xcode if args[0] == "xcodebuild" else "27.0"

    monkeypatch.setattr(native.subprocess, "check_output", command)
    first = native.identity_key(native.build_identity(ios))
    before_cache = native.cache_identities(ios, native.build_identity(ios))
    monkeypatch.setenv("GITHUB_SHA", "candidate-b")
    assert native.identity_key(native.build_identity(ios)) == first
    (root / native.WORKFLOW).write_text("changed only the evidence upload")
    assert native.identity_key(native.build_identity(ios)) == first
    xcode = "Xcode 27.0\nBuild version 27A2"
    assert native.identity_key(native.build_identity(ios)) != first
    xcode = "Xcode 27.0\nBuild version 27A1"
    (root / "ci/patch_ios_companion_e2e.py").write_text("new journey")
    assert native.identity_key(native.build_identity(ios)) != first
    after_cache = native.cache_identities(ios, native.build_identity(ios))
    assert (
        after_cache["IOS_CACHE_COMPATIBILITY"]
        == before_cache["IOS_CACHE_COMPATIBILITY"]
    )
    assert (
        after_cache["IOS_INCREMENTAL_CACHE_ID"]
        == before_cache["IOS_INCREMENTAL_CACHE_ID"]
    )


def test_incremental_prefix_never_crosses_toolchain_or_architecture(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(native.subprocess, "check_output", lambda *a, **kw: "")
    identity = {
        "xcode": "27A",
        "sdk": "27.0",
        "architecture": "arm64",
        "checkout": "/ios",
        "configuration": "Debug",
        "scheme": "Tests-UI",
        "ios_source_sha": "source-a",
    }
    first = native.cache_identities(tmp_path, identity)
    identity["ios_source_sha"] = "source-b"
    second = native.cache_identities(tmp_path, identity)
    assert first["IOS_CACHE_COMPATIBILITY"] == second["IOS_CACHE_COMPATIBILITY"]
    assert first["IOS_INCREMENTAL_CACHE_ID"] != second["IOS_INCREMENTAL_CACHE_ID"]
    for field, value in (("xcode", "27B"), ("architecture", "x86_64")):
        changed = native.cache_identities(tmp_path, {**identity, field: value})
        assert changed["IOS_CACHE_COMPATIBILITY"] != second["IOS_CACHE_COMPATIBILITY"]
