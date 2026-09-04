"""SQLite tool for database queries."""

from __future__ import annotations

import logging
import os
from pathlib import Path
import sqlite3
import time
from typing import Any
from urllib import parse

import voluptuous as vol

from homeassistant.components import recorder
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import llm
from homeassistant.helpers.template import Template

from .base import Function

_LOGGER = logging.getLogger(__name__)
_DEFAULT_MAX_ROWS = 1000
_MAX_MAX_ROWS = 10000
_DEFAULT_QUERY_TIMEOUT_SECONDS = 5.0
_MAX_QUERY_TIMEOUT_SECONDS = 30.0
_DEFAULT_MAX_RESULT_BYTES = 1024 * 1024
_MAX_MAX_RESULT_BYTES = 4 * 1024 * 1024
_PROGRESS_HANDLER_STEPS = 1000

# mode=ro protects the primary database file, but SQLite statements such as ATTACH
# and VACUUM INTO can still have filesystem side effects. Reject every authorizer
# opcode capable of changing database or schema state as a second, statement-level
# boundary. PRAGMA is denied too because some pragmas mutate connection/database state.
_DENIED_SQLITE_ACTIONS = frozenset(
    action
    for action in (
        sqlite3.SQLITE_INSERT,
        sqlite3.SQLITE_UPDATE,
        sqlite3.SQLITE_DELETE,
        sqlite3.SQLITE_CREATE_INDEX,
        sqlite3.SQLITE_CREATE_TABLE,
        sqlite3.SQLITE_CREATE_TEMP_INDEX,
        sqlite3.SQLITE_CREATE_TEMP_TABLE,
        sqlite3.SQLITE_CREATE_TEMP_TRIGGER,
        sqlite3.SQLITE_CREATE_TEMP_VIEW,
        sqlite3.SQLITE_CREATE_TRIGGER,
        sqlite3.SQLITE_CREATE_VIEW,
        sqlite3.SQLITE_CREATE_VTABLE,
        sqlite3.SQLITE_DROP_INDEX,
        sqlite3.SQLITE_DROP_TABLE,
        sqlite3.SQLITE_DROP_TEMP_INDEX,
        sqlite3.SQLITE_DROP_TEMP_TABLE,
        sqlite3.SQLITE_DROP_TEMP_TRIGGER,
        sqlite3.SQLITE_DROP_TEMP_VIEW,
        sqlite3.SQLITE_DROP_TRIGGER,
        sqlite3.SQLITE_DROP_VIEW,
        sqlite3.SQLITE_DROP_VTABLE,
        sqlite3.SQLITE_ALTER_TABLE,
        sqlite3.SQLITE_REINDEX,
        sqlite3.SQLITE_ANALYZE,
        sqlite3.SQLITE_ATTACH,
        sqlite3.SQLITE_DETACH,
        sqlite3.SQLITE_PRAGMA,
        sqlite3.SQLITE_TRANSACTION,
        sqlite3.SQLITE_SAVEPOINT,
    )
)


def _read_only_authorizer(
    action: int,
    _arg1: str | None,
    _arg2: str | None,
    _database: str | None,
    _trigger: str | None,
) -> int:
    """Reject SQLite operations outside the tool's read-only contract."""
    return (
        sqlite3.SQLITE_DENY if action in _DENIED_SQLITE_ACTIONS else sqlite3.SQLITE_OK
    )


def _read_only_sqlite_uri(db_url: str) -> str:
    """Normalize a SQLite path or file URI and force mode=ro."""
    if db_url == ":memory:":
        raise HomeAssistantError("SQLite in-memory databases are not supported")

    if db_url.startswith("file:"):
        uri = db_url
    else:
        uri = Path(db_url).expanduser().resolve().as_uri()

    scheme, netloc, path, query_string, fragment = parse.urlsplit(uri)
    if scheme != "file":
        raise HomeAssistantError("SQLite db_url must be a filesystem path or file: URI")
    query_params = parse.parse_qs(query_string, keep_blank_values=True)
    query_params["mode"] = ["ro"]
    new_query_string = parse.urlencode(query_params, doseq=True)
    return parse.urlunsplit((scheme, netloc, path, new_query_string, fragment))


def _estimated_row_bytes(row: dict[str, Any]) -> int:
    """Return a conservative encoded-size estimate for one SQLite result row."""
    return len(repr(row).encode("utf-8", errors="replace"))


def _set_sqlite_result_limit(conn: sqlite3.Connection, max_result_bytes: int) -> None:
    """Bound any single SQLite string/BLOB before Python can materialize it."""
    conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, max_result_bytes)


def _execute_sqlite_query(
    db_url: str,
    query: str,
    single: bool,
    max_rows: int,
    timeout_seconds: float = _DEFAULT_QUERY_TIMEOUT_SECONDS,
    max_result_bytes: int = _DEFAULT_MAX_RESULT_BYTES,
) -> dict[str, Any] | list[dict[str, Any]]:
    """Execute a time-, row-, and byte-bounded read-only SQLite query."""
    deadline = time.monotonic() + timeout_seconds
    timed_out = False

    def progress_handler() -> int:
        nonlocal timed_out
        if time.monotonic() >= deadline:
            timed_out = True
            return 1
        return 0

    conn = sqlite3.connect(db_url, uri=True)
    try:
        # Apply SQLite's own length ceiling before executing user SQL so oversized
        # strings/BLOBs are rejected by the engine rather than materialized first.
        _set_sqlite_result_limit(conn, max_result_bytes)
        # Set the built-in read-only guard before installing the authorizer because
        # the authorizer deliberately rejects user-issued PRAGMA statements.
        conn.execute("PRAGMA query_only = ON")
        conn.set_authorizer(_read_only_authorizer)
        conn.set_progress_handler(progress_handler, _PROGRESS_HANDLER_STEPS)
        try:
            cursor = conn.execute(query)
            if cursor.description is None:
                raise HomeAssistantError("SQLite query did not return any columns")

            names = [description[0] for description in cursor.description]
            if single:
                row = cursor.fetchone()
                if row is None:
                    return {}
                result = {name: val for name, val in zip(names, row, strict=False)}
                result_bytes = _estimated_row_bytes(result)
                if result_bytes > max_result_bytes:
                    raise HomeAssistantError(
                        "SQLite query result exceeded the configured result-size "
                        f"limit of {max_result_bytes} bytes"
                    )
                return result

            results: list[dict[str, Any]] = []
            result_bytes = 2
            for row_number, row in enumerate(cursor, start=1):
                if row_number > max_rows:
                    raise HomeAssistantError(
                        f"SQLite query returned more than {max_rows} rows; "
                        "add a LIMIT clause or increase max_rows"
                    )
                item = {name: val for name, val in zip(names, row, strict=False)}
                result_bytes += _estimated_row_bytes(item) + 1
                if result_bytes > max_result_bytes:
                    raise HomeAssistantError(
                        "SQLite query result exceeded the configured result-size "
                        f"limit of {max_result_bytes} bytes"
                    )
                results.append(item)
            return results
        except sqlite3.DataError as err:
            if "too big" in str(err).lower():
                raise HomeAssistantError(
                    "SQLite query result exceeded the configured result-size "
                    f"limit of {max_result_bytes} bytes"
                ) from err
            raise
        except sqlite3.OperationalError as err:
            if timed_out:
                raise HomeAssistantError(
                    "SQLite query exceeded the configured execution deadline of "
                    f"{timeout_seconds:g} seconds"
                ) from err
            raise
        finally:
            conn.set_progress_handler(None, 0)
    finally:
        conn.close()


class SqliteFunction(Function):
    def __init__(self) -> None:
        """Initialize sqlite tool."""
        super().__init__(
            vol.Schema(
                {
                    vol.Optional("query"): str,
                    vol.Optional("db_url"): str,
                    vol.Optional("single"): bool,
                    vol.Optional("max_rows", default=_DEFAULT_MAX_ROWS): vol.All(
                        vol.Coerce(int), vol.Range(min=1, max=_MAX_MAX_ROWS)
                    ),
                    vol.Optional(
                        "timeout", default=_DEFAULT_QUERY_TIMEOUT_SECONDS
                    ): vol.All(
                        vol.Coerce(float),
                        vol.Range(min=0.1, max=_MAX_QUERY_TIMEOUT_SECONDS),
                    ),
                    vol.Optional(
                        "max_result_bytes", default=_DEFAULT_MAX_RESULT_BYTES
                    ): vol.All(
                        vol.Coerce(int),
                        vol.Range(min=1024, max=_MAX_MAX_RESULT_BYTES),
                    ),
                }
            )
        )

    def is_exposed(
        self, entity_id: str, exposed_entities: list[dict[str, Any]]
    ) -> bool:
        return any(
            exposed_entity["entity_id"] == entity_id
            for exposed_entity in exposed_entities
        )

    def is_exposed_entity_in_query(
        self, query: str, exposed_entities: list[dict[str, Any]]
    ) -> bool:
        exposed_entity_ids = list(
            map(lambda e: f"'{e['entity_id']}'", exposed_entities)
        )
        return any(
            exposed_entity_id in query for exposed_entity_id in exposed_entity_ids
        )

    def raise_error(self, msg: str = "Unexpected error occurred.") -> None:
        raise HomeAssistantError(msg)

    def get_default_db_url(self, hass: HomeAssistant) -> str:
        db_file_path = os.path.join(hass.config.config_dir, recorder.DEFAULT_DB_FILE)
        return _read_only_sqlite_uri(db_file_path)

    def set_url_read_only(self, url: str) -> str:
        return _read_only_sqlite_uri(url)

    async def execute(
        self,
        hass: HomeAssistant,
        function_config: dict[str, Any],
        arguments: dict[str, Any],
        llm_context: llm.LLMContext | None,
        exposed_entities: list[dict[str, Any]],
    ) -> dict[str, Any] | list[dict[str, Any]]:
        db_url = self.set_url_read_only(
            function_config.get("db_url", self.get_default_db_url(hass))
        )
        query = function_config.get("query", "{{query}}")

        template_arguments = {
            "is_exposed": lambda e: self.is_exposed(e, exposed_entities),
            "is_exposed_entity_in_query": lambda q: self.is_exposed_entity_in_query(
                q, exposed_entities
            ),
            "exposed_entities": exposed_entities,
            "raise": self.raise_error,
        }
        template_arguments.update(arguments)

        q = Template(query, hass).async_render(template_arguments)
        _LOGGER.debug("Rendered SQLite query: %s", q)

        try:
            return await hass.async_add_executor_job(
                _execute_sqlite_query,
                db_url,
                q,
                function_config.get("single") is True,
                int(function_config.get("max_rows", _DEFAULT_MAX_ROWS)),
                float(function_config.get("timeout", _DEFAULT_QUERY_TIMEOUT_SECONDS)),
                int(function_config.get("max_result_bytes", _DEFAULT_MAX_RESULT_BYTES)),
            )
        except sqlite3.Error as err:
            raise HomeAssistantError(f"SQLite query failed: {err}") from err
