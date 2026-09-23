"""Guard the CI image selection rules that affect PR test coverage and setup time."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from types import SimpleNamespace

from ci import check_ha_dev_environment, check_ha_dev_image, typecheck_image_key


def test_ha_dev_image_requires_recent_snapshot(monkeypatch):
    current_sha = "current"

    def inspect(created: datetime, snapshot_sha: str):
        labels = {
            "org.opencontainers.image.created": created.isoformat(),
            "org.opencontainers.image.revision": snapshot_sha,
        }
        monkeypatch.setattr(
            check_ha_dev_image.subprocess,
            "run",
            lambda *args, **kwargs: SimpleNamespace(stdout=json.dumps(labels)),
        )

    now = datetime.now(UTC)
    inspect(now - timedelta(hours=23), "recent")
    assert check_ha_dev_image.main("image", current_sha) == 0

    inspect(now - timedelta(hours=25), "stale")
    assert check_ha_dev_image.main("image", current_sha) == 1

    inspect(now - timedelta(hours=25), current_sha)
    assert check_ha_dev_image.main("image", current_sha) == 0


def test_ha_dev_allows_fixture_pin_only_when_core_requires_installed_version(
    monkeypatch,
):
    monkeypatch.setattr(
        check_ha_dev_environment,
        "requires",
        lambda package: ["SQLAlchemy==2.0.53"],
    )
    monkeypatch.setattr(
        check_ha_dev_environment,
        "version",
        lambda package: "2.0.53",
    )
    fixture_mismatch = (
        "pytest-homeassistant-custom-component 0.13.366 has requirement "
        "SQLAlchemy==2.0.52, but you have sqlalchemy 2.0.53."
    )
    assert check_ha_dev_environment.allowed_plugin_mismatch(fixture_mismatch)
    assert not check_ha_dev_environment.allowed_plugin_mismatch(
        "other-package 1.0 has requirement SQLAlchemy==2.0.52, "
        "but you have sqlalchemy 2.0.53."
    )

    monkeypatch.setattr(check_ha_dev_environment, "version", lambda package: "2.0.54")
    assert not check_ha_dev_environment.allowed_plugin_mismatch(fixture_mismatch)


def test_typecheck_image_key_tracks_dependencies_not_core_commit(monkeypatch, capsys):
    for name in typecheck_image_key.BUILD_INPUTS:
        monkeypatch.setenv(name, "1.0")
    monkeypatch.setenv("HA_CORE_SHA", "first")
    typecheck_image_key.main()
    first = capsys.readouterr().out.strip()

    monkeypatch.setenv("HA_CORE_SHA", "second")
    typecheck_image_key.main()
    assert capsys.readouterr().out.strip() == first

    monkeypatch.setenv("OPENAI_VERSION", "2.0")
    typecheck_image_key.main()
    assert capsys.readouterr().out.strip() != first
