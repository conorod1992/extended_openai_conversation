"""String parameters retain authored boundary whitespace through hydration."""

from contextlib import closing
import sqlite3

import pytest

from custom_components.extended_openai_conversation_responses.functions.sqlite import (
    SqliteFunction,
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
