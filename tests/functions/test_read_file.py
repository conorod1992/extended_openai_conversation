"""Tests for ReadFileFunction using yaml definitions."""

import pytest

from custom_components.extended_openai_conversation_responses.functions import (
    ReadFileFunction,
)
from tests.helpers import prepare_function_tool_from_yaml


class TestReadFileFunctionYaml:
    """Test ReadFileFunction using yaml definitions."""

    @pytest.fixture
    def function(self):
        """Create ReadFileFunction instance."""
        return ReadFileFunction()

    async def test_read_file_success_from_yaml(
        self, hass, function, exposed_entities, llm_context, tmp_path
    ):
        """Test reading a file successfully from yaml definition."""
        workdir = tmp_path / "extended_openai_conversation_responses"
        test_file = workdir / "test.txt"
        test_content = "Hello, World!\nThis is a test file."
        test_file.write_text(test_content)

        function_tool = prepare_function_tool_from_yaml("read_file_example.yaml", 0)
        function_config = function_tool["function"]

        arguments = {"filename": "test.txt"}

        result = await function.execute(
            hass, function_config, arguments, llm_context, exposed_entities
        )

        assert "content" in result
        assert result["content"] == test_content
        assert "size" in result
        assert result["size"] == len(test_content.encode("utf-8"))

    async def test_read_file_absolute_path_from_yaml(
        self, hass, function, exposed_entities, llm_context, tmp_path
    ):
        """Test reading file with absolute path from yaml."""
        workdir = tmp_path / "extended_openai_conversation_responses"
        test_file = workdir / "absolute_test.txt"
        test_content = "Absolute path test"
        test_file.write_text(test_content)

        function_tool = prepare_function_tool_from_yaml("read_file_example.yaml", 1)
        function_config = function_tool["function"]

        arguments = {"filepath": str(test_file)}

        result = await function.execute(
            hass, function_config, arguments, llm_context, exposed_entities
        )

        assert "content" in result
        assert result["content"] == test_content

    async def test_read_file_not_found(
        self, hass, function, exposed_entities, llm_context
    ):
        """Test reading a file that doesn't exist."""
        function_tool = prepare_function_tool_from_yaml("read_file_example.yaml", 0)
        function_config = function_tool["function"]

        arguments = {"filename": "nonexistent.txt"}

        result = await function.execute(
            hass, function_config, arguments, llm_context, exposed_entities
        )

        assert "error" in result
        assert "not found" in result["error"].lower()

    async def test_read_file_is_directory(
        self, hass, function, exposed_entities, llm_context, tmp_path
    ):
        """Test reading a directory instead of file."""
        workdir = tmp_path / "extended_openai_conversation_responses"
        test_dir = workdir / "testdir"
        test_dir.mkdir()

        function_tool = prepare_function_tool_from_yaml("read_file_example.yaml", 0)
        function_config = function_tool["function"]

        arguments = {"filename": "testdir"}

        result = await function.execute(
            hass, function_config, arguments, llm_context, exposed_entities
        )

        assert "error" in result
        assert "not a file" in result["error"].lower()

    async def test_read_file_too_large(
        self, hass, function, exposed_entities, llm_context, tmp_path
    ):
        """Test reading a file that exceeds size limit."""
        workdir = tmp_path / "extended_openai_conversation_responses"
        workdir.mkdir(parents=True, exist_ok=True)
        test_file = workdir / "large.txt"

        # Create a file larger than FILE_READ_SIZE_LIMIT (1 MB)
        large_content = "A" * (1024 * 1024 + 100)
        test_file.write_text(large_content)

        function_tool = prepare_function_tool_from_yaml("read_file_example.yaml", 0)
        function_config = function_tool["function"]

        arguments = {"filename": "large.txt"}

        result = await function.execute(
            hass, function_config, arguments, llm_context, exposed_entities
        )

        assert "error" in result
        assert "too large" in result["error"].lower()

    async def test_read_file_outside_allowed_dir(
        self, hass, function, exposed_entities, llm_context, tmp_path
    ):
        """Test that reading files outside allowed directories is blocked."""
        external_file = tmp_path / "external.txt"
        external_file.write_text("This should not be accessible")

        function_tool = prepare_function_tool_from_yaml("read_file_example.yaml", 1)
        function_config = function_tool["function"]

        arguments = {"filepath": str(external_file)}

        result = await function.execute(
            hass, function_config, arguments, llm_context, exposed_entities
        )

        assert "error" in result
        assert (
            "access denied" in result["error"].lower()
            or "not in allowed" in result["error"].lower()
        )

    async def test_read_file_absolute_path_outside_working_directory_from_yaml(
        self, hass, function, exposed_entities, llm_context, tmp_path
    ):
        """Test reading file with absolute path outside working directory from yaml."""
        # Create a file outside the working directory
        external_dir = tmp_path / "external"
        external_dir.mkdir()
        test_file = external_dir / "external_test.txt"
        test_content = "External file content"
        test_file.write_text(test_content)

        function_tool = prepare_function_tool_from_yaml("read_file_example.yaml", 2)
        function_config = function_tool["function"]

        arguments = {"filepath": str(test_file)}

        result = await function.execute(
            hass, function_config, arguments, llm_context, exposed_entities
        )

        # This should fail because the file is outside the allowed directory
        assert "error" in result
        assert (
            "access denied" in result["error"].lower()
            or "not in allowed" in result["error"].lower()
        )

    async def test_read_file_with_custom_allow_dir_from_yaml(
        self, hass, function, exposed_entities, llm_context
    ):
        """Test reading file from custom allowed directory from yaml."""
        import os

        # Use /tmp directory as specified in yaml
        test_file_path = os.path.join("/tmp", "custom_read_test.txt")
        test_content = "Custom directory content"

        with open(test_file_path, "w") as f:
            f.write(test_content)

        try:
            function_tool = prepare_function_tool_from_yaml("read_file_example.yaml", 3)
            function_config = function_tool["function"]

            arguments = {
                "filepath": test_file_path,
            }

            result = await function.execute(
                hass, function_config, arguments, llm_context, exposed_entities
            )

            assert "content" in result
            assert result["content"] == test_content
        finally:
            # Clean up
            if os.path.exists(test_file_path):
                os.remove(test_file_path)

    async def test_read_file_template_path(
        self, hass, function, exposed_entities, llm_context, tmp_path
    ):
        """Test reading file with templated path."""
        workdir = tmp_path / "extended_openai_conversation_responses"
        test_file = workdir / "templated.txt"
        test_content = "Hello, World!\nThis is a test file."
        test_file.write_text(test_content)

        function_tool = prepare_function_tool_from_yaml("read_file_example.yaml", 0)
        function_config = function_tool["function"]

        arguments = {"filename": "templated.txt"}

        result = await function.execute(
            hass, function_config, arguments, llm_context, exposed_entities
        )

        assert "content" in result
        assert result["content"] == test_content

    async def test_read_file_unicode_content_from_yaml(
        self, hass, function, exposed_entities, llm_context, tmp_path
    ):
        """Test reading file with unicode content from yaml."""
        workdir = tmp_path / "extended_openai_conversation_responses"
        test_file = workdir / "unicode.txt"
        test_content = "Hello 世界 🌍 مرحبا"
        test_file.write_text(test_content, encoding="utf-8")

        function_tool = prepare_function_tool_from_yaml("read_file_example.yaml", 4)
        function_config = function_tool["function"]

        arguments = {}

        result = await function.execute(
            hass, function_config, arguments, llm_context, exposed_entities
        )

        assert "content" in result
        assert result["content"] == test_content
