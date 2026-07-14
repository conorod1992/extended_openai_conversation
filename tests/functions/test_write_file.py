"""Tests for WriteFileFunction using yaml definitions."""

import pytest

from custom_components.extended_openai_conversation_responses.functions import (
    WriteFileFunction,
)
from tests.helpers import prepare_function_tool_from_yaml


class TestWriteFileFunctionYaml:
    """Test WriteFileFunction using yaml definitions."""

    @pytest.fixture
    def function(self):
        """Create WriteFileFunction instance."""
        return WriteFileFunction()

    async def test_write_file_success_from_yaml(
        self, hass, function, exposed_entities, llm_context, tmp_path
    ):
        """Test writing a file successfully from yaml definition."""
        workdir = tmp_path / "extended_openai_conversation_responses"
        test_file = workdir / "new_file.txt"

        function_tool = prepare_function_tool_from_yaml("write_file_example.yaml", 0)
        function_config = function_tool["function"]

        test_content = "This is new content"
        arguments = {"filename": "new_file.txt", "content": test_content}

        result = await function.execute(
            hass, function_config, arguments, llm_context, exposed_entities
        )

        assert result["success"] is True
        assert "path" in result
        assert "bytes_written" in result
        assert result["bytes_written"] == len(test_content.encode("utf-8"))
        assert test_file.exists()
        assert test_file.read_text() == test_content

    async def test_write_file_overwrite_from_yaml(
        self, hass, function, exposed_entities, llm_context, tmp_path
    ):
        """Test overwriting an existing file from yaml."""
        workdir = tmp_path / "extended_openai_conversation_responses"
        test_file = workdir / "existing.txt"
        test_file.write_text("old content")

        function_tool = prepare_function_tool_from_yaml("write_file_example.yaml", 0)
        function_config = function_tool["function"]

        new_content = "New content"
        arguments = {"filename": "existing.txt", "content": new_content}

        result = await function.execute(
            hass, function_config, arguments, llm_context, exposed_entities
        )

        assert result["success"] is True
        assert test_file.read_text() == new_content

    async def test_write_file_absolute_path_from_yaml(
        self, hass, function, exposed_entities, llm_context, tmp_path
    ):
        """Test writing file with absolute path from yaml."""
        workdir = tmp_path / "extended_openai_conversation_responses"
        test_file = workdir / "absolute.txt"

        function_tool = prepare_function_tool_from_yaml("write_file_example.yaml", 1)
        function_config = function_tool["function"]

        test_content = "Absolute path content"
        arguments = {"filepath": str(test_file), "content": test_content}

        result = await function.execute(
            hass, function_config, arguments, llm_context, exposed_entities
        )

        assert result["success"] is True
        assert test_file.read_text() == test_content

    async def test_write_file_outside_allowed_dir(
        self, hass, function, exposed_entities, llm_context, tmp_path
    ):
        """Test that writing files outside allowed directories is blocked."""
        external_file = tmp_path / "external.txt"

        function_tool = prepare_function_tool_from_yaml("write_file_example.yaml", 1)
        function_config = function_tool["function"]

        arguments = {
            "filepath": str(external_file),
            "content": "This should not be written",
        }

        result = await function.execute(
            hass, function_config, arguments, llm_context, exposed_entities
        )

        assert "error" in result
        assert (
            "access denied" in result["error"].lower()
            or "not in allowed" in result["error"].lower()
        )
        assert not external_file.exists()

    async def test_write_file_with_custom_allow_dir_from_yaml(
        self, hass, function, exposed_entities, llm_context
    ):
        """Test writing file to custom allowed directory from yaml."""
        import os

        # Use /tmp directory as specified in yaml
        test_file_path = os.path.join("/tmp", "custom_write_test.txt")

        try:
            function_tool = prepare_function_tool_from_yaml(
                "write_file_example.yaml", 2
            )
            function_config = function_tool["function"]

            test_content = "Custom directory write"
            arguments = {
                "filepath": test_file_path,
                "content": test_content,
            }

            result = await function.execute(
                hass, function_config, arguments, llm_context, exposed_entities
            )

            assert result["success"] is True
            with open(test_file_path) as f:
                assert f.read() == test_content
        finally:
            # Clean up
            if os.path.exists(test_file_path):
                os.remove(test_file_path)

    async def test_write_file_template_path(
        self, hass, function, exposed_entities, llm_context, tmp_path
    ):
        """Test writing file with templated path."""
        workdir = tmp_path / "extended_openai_conversation_responses"
        test_file = workdir / "templated.txt"

        function_tool = prepare_function_tool_from_yaml("write_file_example.yaml", 0)
        function_config = function_tool["function"]

        arguments = {"filename": "templated.txt", "content": "Templated content"}

        result = await function.execute(
            hass, function_config, arguments, llm_context, exposed_entities
        )

        assert result["success"] is True
        assert test_file.read_text() == "Templated content"

    async def test_write_file_unicode_content_from_yaml(
        self, hass, function, exposed_entities, llm_context, tmp_path
    ):
        """Test writing file with unicode content from yaml."""
        workdir = tmp_path / "extended_openai_conversation_responses"
        test_file = workdir / "unicode.txt"

        function_tool = prepare_function_tool_from_yaml("write_file_example.yaml", 3)
        function_config = function_tool["function"]

        test_content = "Hello 世界 🌍 مرحبا"
        arguments = {"filename": "unicode.txt", "content": test_content}

        result = await function.execute(
            hass, function_config, arguments, llm_context, exposed_entities
        )

        assert result["success"] is True
        assert test_file.read_text(encoding="utf-8") == test_content

    async def test_write_file_empty_content_from_yaml(
        self, hass, function, exposed_entities, llm_context, tmp_path
    ):
        """Test writing file with empty content from yaml."""
        workdir = tmp_path / "extended_openai_conversation_responses"
        test_file = workdir / "empty.txt"

        function_tool = prepare_function_tool_from_yaml("write_file_example.yaml", 4)
        function_config = function_tool["function"]

        arguments = {"filename": "empty.txt", "content": ""}

        result = await function.execute(
            hass, function_config, arguments, llm_context, exposed_entities
        )

        assert result["success"] is True
        assert result["bytes_written"] == 0
        assert test_file.read_text() == ""

    async def test_write_file_multiline_content_from_yaml(
        self, hass, function, exposed_entities, llm_context, tmp_path
    ):
        """Test writing file with multiline content from yaml."""
        workdir = tmp_path / "extended_openai_conversation_responses"
        test_file = workdir / "multiline.txt"

        function_tool = prepare_function_tool_from_yaml("write_file_example.yaml", 5)
        function_config = function_tool["function"]

        test_content = "Line 1\nLine 2\nLine 3"
        arguments = {"filename": "multiline.txt", "content": test_content}

        result = await function.execute(
            hass, function_config, arguments, llm_context, exposed_entities
        )

        assert result["success"] is True
        assert test_file.read_text() == test_content
