"""The implicit SQLite file is Recorder's file; overrides never need Recorder."""

import sqlite3
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses.functions import (
    sqlite as module,
)
from homeassistant.exceptions import HomeAssistantError


def make_db(path, value):
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE marker (value TEXT)")
        conn.execute("INSERT INTO marker VALUES (?)", (value,))
    return path


@pytest.mark.parametrize(
    "location",
    ["custom", "default", "relative", "uri", "relative_uri", "uri_plain", "special"],
)
async def test_implicit_file_matches_sqlalchemy_semantics(
    hass, tmp_path, monkeypatch, location
):
    monkeypatch.chdir(tmp_path)
    default = make_db(tmp_path / "home-assistant_v2.db", "legacy")
    custom = make_db(tmp_path / "custom.db", "live")
    special = make_db(tmp_path / "space # percent %.db", "special")
    urls = {
        "custom": f"sqlite:///{custom.as_posix()}",
        "default": f"sqlite:///{default.as_posix()}",
        "relative": "sqlite:///custom.db",
        "uri": f"sqlite:///{custom.as_uri()}?mode=rw&cache=shared&uri=true&timeout=20",
        "relative_uri": "sqlite:///file:custom.db?uri=true&mode=rw",
        "uri_plain": "sqlite:///custom.db?uri=true",
        "special": f"sqlite:///{special.as_uri()}?uri=true",
    }
    monkeypatch.setattr(
        module.recorder,
        "get_instance",
        lambda _: SimpleNamespace(db_url=urls[location]),
    )
    assert await module.SqliteFunction().execute(
        hass, {"query": "SELECT value FROM marker"}, {}, None, []
    ) == [
        {
            "value": "legacy"
            if location == "default"
            else "special"
            if location == "special"
            else "live"
        }
    ]


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://user:secret@host/db",
        "mysql://user:secret@host/db",
        "sqlite://",
        "sqlite:///:memory:",
        "sqlite:///file::memory:?uri=true",
        "sqlite:///file:%3Amemory%3A?uri=true",
        "sqlite:///file:named?mode=memory&uri=true",
        "sqlite:///file:?uri=true",
        "sqlite+aiosqlite:///file.db",
        "invalid secret url",
        "sqlite://user:secret@host/db",
        "sqlite:///file://user:secret@host/db?uri=true",
        "sqlite:///file:db?uri=invalid",
        None,
    ],
)
async def test_unsupported_implicit_default_fails_without_legacy_fallback(
    hass, tmp_path, monkeypatch, url
):
    make_db(tmp_path / "home-assistant_v2.db", "stale")
    monkeypatch.setattr(
        module.recorder, "get_instance", lambda _: SimpleNamespace(db_url=url)
    )
    with pytest.raises(HomeAssistantError) as error:
        await module.SqliteFunction().execute(
            hass, {"query": "SELECT value FROM marker"}, {}, None, []
        )
    assert "secret" not in str(error.value)
    hass.async_add_executor_job.assert_not_awaited()


async def test_missing_recorder_never_reads_stale_default(hass, tmp_path):
    make_db(tmp_path / "home-assistant_v2.db", "stale")
    module.recorder.get_instance.cache_clear()
    with pytest.raises(HomeAssistantError, match="available Recorder"):
        await module.SqliteFunction().execute(
            hass, {"query": "SELECT value FROM marker"}, {}, None, []
        )
    hass.async_add_executor_job.assert_not_awaited()


@pytest.mark.parametrize("backend", [None, "postgresql://user:secret@host/db"])
@pytest.mark.parametrize("uri", [False, True])
async def test_override_works_independently_of_recorder(
    hass, tmp_path, monkeypatch, backend, uri
):
    path = make_db(tmp_path / "override.db", "explicit")

    def instance(_):
        if backend is None:
            raise KeyError("not running")
        return SimpleNamespace(db_url=backend)

    monkeypatch.setattr(module.recorder, "get_instance", instance)
    assert await module.SqliteFunction().execute(
        hass,
        {
            "db_url": path.as_uri() if uri else str(path),
            "query": "SELECT value FROM marker",
        },
        {},
        None,
        [],
    ) == [{"value": "explicit"}]


@pytest.mark.parametrize(
    "override",
    [
        "",
        None,
        ":memory:",
        "sqlite:///wrong.db",
        "file::memory:?cache=shared",
        "file:%3Amemory%3A?cache=shared",
        "file:named?mode=memory",
        "postgresql://user:secret@host/db",
        "missing.db",
        "file://user:secret@host/db",
    ],
)
async def test_invalid_override_never_resolves_default(hass, monkeypatch, override):
    function = module.SqliteFunction()

    def forbidden(_):
        pytest.fail("default resolver must be lazy, including invalid overrides")

    monkeypatch.setattr(function, "get_default_db_url", forbidden)
    with pytest.raises(HomeAssistantError) as error:
        await function.execute(
            hass, {"db_url": override, "query": "SELECT 1"}, {}, None, []
        )
    assert "secret" not in str(error.value)


async def test_valid_override_never_calls_default_resolver(hass, tmp_path, monkeypatch):
    path = make_db(tmp_path / "override.db", "explicit")
    function = module.SqliteFunction()
    monkeypatch.setattr(
        function, "get_default_db_url", lambda _: pytest.fail("eager default lookup")
    )
    assert await function.execute(
        hass, {"db_url": str(path), "query": "SELECT value FROM marker"}, {}, None, []
    ) == [{"value": "explicit"}]
