import hashlib
import json

from ci.environment_fingerprint import _matches, identity


def _identity(
    tmp_path, *, requirements="a==1\nb>=2\n", manifest=None, recipe="install"
):
    requirements_path = tmp_path / "requirements.txt"
    requirements_path.write_text(requirements, encoding="utf-8")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            manifest or {"requirements": ["c==3"], "name": "before", "version": "1"}
        ),
        encoding="utf-8",
    )
    recipe_path = tmp_path / "Dockerfile"
    recipe_path.write_text(recipe, encoding="utf-8")
    return identity(
        requirements_path,
        manifest_path,
        [recipe_path],
        "3.14.8",
        "python:3.14-slim-bookworm",
        [],
    )


def test_fingerprint_ignores_formatting_and_manifest_metadata(tmp_path):
    initial = _identity(tmp_path)
    equivalent = _identity(
        tmp_path,
        requirements="# comment\nb>=2  # note\na==1\n",
        manifest={"requirements": ["c==3"], "name": "after", "version": "2"},
    )

    assert initial == equivalent


def test_fingerprint_changes_for_dependency_recipe_or_runtime_identity(tmp_path):
    initial = _identity(tmp_path)
    changed_dependency = _identity(
        tmp_path,
        manifest={
            "requirements": ["c==4"],
            "dependencies": ["new_integration"],
            "name": "before",
        },
    )
    changed_recipe = _identity(tmp_path, recipe="changed installer")

    assert changed_dependency["sha256"] != initial["sha256"]
    assert changed_recipe["sha256"] != initial["sha256"]

    requirements_path = tmp_path / "requirements.txt"
    manifest_path = tmp_path / "manifest.json"
    recipe_path = tmp_path / "Dockerfile"
    changed_python = identity(
        requirements_path,
        manifest_path,
        [recipe_path],
        "3.14.9",
        "python:3.14-slim-bookworm",
        [],
    )
    assert changed_python["sha256"] != initial["sha256"]


def test_fingerprint_rejects_dependency_recipe_runtime_and_system_mismatches(tmp_path):
    built = _identity(tmp_path)
    built["system_packages"] = ["libc6=2.36"]
    payload = {key: value for key, value in built.items() if key != "sha256"}
    built["sha256"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    current = {**built, "system_packages": ["libc6=2.36", "libwebkit=1"]}

    assert not _matches(built, current)
    assert not _matches(built, {**current, "base_image": "python:other"})
    assert not _matches(built, {**current, "system_packages": ["libc6=2.37"]})
