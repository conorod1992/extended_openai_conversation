"""String parameters retain authored boundary whitespace through hydration."""

from contextlib import closing
import sqlite3

import pytest

from custom_components.extended_openai_conversation_responses.entity import (
    _normalize_function_result,
)
from custom_components.extended_openai_conversation_responses.functions.sqlite import (
    SqliteFunction,
    _execute_sqlite_query,
    _read_only_sqlite_uri,
)


@pytest.mark.parametrize(
    ("source", "arguments", "expected"),
    [
        ("  padded  ", {}, "  padded  "),
        ("\tvalue\t", {}, "\tvalue\t"),
        ("\nvalue\n", {}, "\nvalue\n"),
        (" \t\n ", {}, " \t\n "),
        ("  {{ value }}  ", {"value": "padded"}, "  padded  "),
        ("{{ value }}", {"value": "  padded  "}, "  padded  "),
        ("{{ value }}", {"value": "None"}, "None"),
    ],
)
@pytest.mark.parametrize("hydrate", [False, True])
async def test_exact_string_parameter_selects_intended_row(
    hass, tmp_path, source, arguments, expected, hydrate
):
    database = tmp_path / "exact.db"
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("CREATE TABLE lookup (needle TEXT, result TEXT)")
        connection.execute("INSERT INTO lookup VALUES (?, 'intended')", (expected,))
        if expected.strip() != expected:
            connection.execute(
                "INSERT INTO lookup VALUES (?, 'wrong')", (expected.strip(),)
            )
        connection.commit()
    function = SqliteFunction()
    config = {
        "type": "sqlite",
        "db_url": str(database),
        "query": "SELECT result FROM lookup WHERE needle = :needle",
        "single": True,
        "parameters": {"needle": source},
        "string_parameters": ["needle"],
    }
    if hydrate:
        config = function.validate_schema(config)
    assert await function.execute(hass, config, arguments, None, []) == {
        "result": "intended"
    }


async def test_ordinary_parameters_keep_native_scalar_parsing(hass, tmp_path):
    hass.config.legacy_templates = False
    database = tmp_path / "typed.db"
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("CREATE TABLE unused (value TEXT)")
        connection.commit()
    function = SqliteFunction()
    config = function.validate_schema(
        {
            "type": "sqlite",
            "db_url": str(database),
            "query": "SELECT typeof(:number) AS number, typeof(:nothing) AS null_value",
            "single": True,
            "parameters": {"number": "  {{ value }}  ", "nothing": "None"},
        }
    )
    assert await function.execute(hass, config, {"value": "5"}, None, []) == {
        "number": "integer",
        "null_value": "null",
    }


def test_blob_columns_are_typed_base64_and_keep_rows_structured(tmp_path):
    """Mixed SQLite rows remain JSON-shaped when they contain BLOB columns."""
    database = tmp_path / "blobs.db"
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("CREATE TABLE sample (label TEXT, payload BLOB)")
        connection.executemany(
            "INSERT INTO sample VALUES (?, ?)",
            [("first", bytes.fromhex("00ff")), ("second", b"text\x00blob")],
        )
        connection.commit()

    result = _execute_sqlite_query(
        _read_only_sqlite_uri(str(database)),
        "SELECT label, payload FROM sample ORDER BY label",
        single=False,
        max_rows=10,
    )

    expected = [
        {"label": "first", "payload": {"type": "base64", "data": "AP8="}},
        {"label": "second", "payload": {"type": "base64", "data": "dGV4dABibG9i"}},
    ]
    assert result == expected
    # This is the same normalization boundary that forwards Function results to
    # the model; it must preserve the row structure rather than stringify it.
    assert _normalize_function_result(result) == expected
