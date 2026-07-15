"""Tests for SqliteFunction using yaml definitions."""

import pytest

# Import Tools and test helpers
from custom_components.extended_openai_conversation_responses.functions import (
    SqliteFunction,
)
from tests.helpers import prepare_function_tool_from_yaml


class TestSqliteFunctionYaml:
    """Test SqliteFunction using yaml definitions."""

    @pytest.fixture
    def function(self):
        """Create TemplateFunction instance."""
        return SqliteFunction()

    async def test_execute_sqlite_from_yaml(
        self, hass, function, temp_db_path, exposed_entities, llm_context
    ):
        """Test SQLite query execution from yaml definition."""
        import sqlite3

        # Add sensor.living_room_temperature to exposed entities for this test
        exposed_entities = [
            *exposed_entities,
            {
                "entity_id": "sensor.living_room_temperature",
                "name": "Living Room Temperature",
                "state": "23.5",
                "aliases": [],
            },
        ]

        # Update the database schema to match the new query
        conn = sqlite3.connect(str(temp_db_path))
        cursor = conn.cursor()

        # Drop old table and create new one with last_updated_ts
        cursor.execute("DROP TABLE IF EXISTS states")
        cursor.execute("""
            CREATE TABLE states (
                entity_id TEXT,
                state TEXT,
                last_updated_ts INTEGER
            )
        """)

        # Insert test data with timestamps
        import time

        current_time = int(time.time())
        cursor.executemany(
            "INSERT INTO states VALUES (?, ?, ?)",
            [
                ("sensor.living_room_temperature", "22.5", current_time - 3600),
                ("sensor.living_room_temperature", "23.0", current_time - 1800),
                ("sensor.living_room_temperature", "23.5", current_time - 900),
            ],
        )
        conn.commit()
        conn.close()

        # Load function from yaml
        function_tool = prepare_function_tool_from_yaml("sqlite_example.yaml")
        function_config = function_tool["function"]

        # Add db_url to function_config
        function_config["db_url"] = f"file:{temp_db_path}"

        # Arguments based on yaml spec parameters
        arguments = {"sensor_name": "sensor.living_room_temperature", "hours": 24}

        result = await function.execute(
            hass, function_config, arguments, llm_context, exposed_entities
        )

        # Result should be a list of temperature records
        assert isinstance(result, list)
        assert len(result) > 0
        # Most recent record should be first (DESC order)
        assert result[0]["state"] == "23.5"
