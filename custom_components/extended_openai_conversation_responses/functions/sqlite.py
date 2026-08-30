"""SQLite tool for database queries."""

from __future__ import annotations

import logging
import os
import sqlite3
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


def _execute_sqlite_query(
    db_url: str, query: str, single: bool, max_rows: int
) -> dict[str, Any] | list[dict[str, Any]]:
    """Execute a bounded read-only SQLite query outside the event loop."""
    with sqlite3.connect(db_url, uri=True) as conn:
        cursor = conn.execute(query)
        if cursor.description is None:
            raise HomeAssistantError("SQLite query did not return any columns")

        names = [description[0] for description in cursor.description]
        if single:
            row = cursor.fetchone()
            if row is None:
                return {}
            return {name: val for name, val in zip(names, row, strict=False)}

        rows = cursor.fetchmany(max_rows + 1)
        if len(rows) > max_rows:
            raise HomeAssistantError(
                f"SQLite query returned more than {max_rows} rows; "
                "add a LIMIT clause or increase max_rows"
            )
        return [
            {name: val for name, val in zip(names, row, strict=False)} for row in rows
        ]


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
        return f"file:{db_file_path}?mode=ro"

    def set_url_read_only(self, url: str) -> str:
        scheme, netloc, path, query_string, fragment = parse.urlsplit(url)
        query_params = parse.parse_qs(query_string)

        query_params["mode"] = ["ro"]
        new_query_string = parse.urlencode(query_params, doseq=True)

        return parse.urlunsplit((scheme, netloc, path, new_query_string, fragment))

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
            )
        except sqlite3.Error as err:
            raise HomeAssistantError(f"SQLite query failed: {err}") from err
