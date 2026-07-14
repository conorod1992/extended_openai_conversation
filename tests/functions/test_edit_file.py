"""Tests for EditFileFunction using yaml definitions."""

import pytest

from custom_components.extended_openai_conversation_responses.functions import (
    EditFileFunction,
    get_function,
)
from tests.helpers import prepare_function_tool_from_yaml


class TestEditFileFunctionYaml:
    """Test EditFileFunction using yaml definitions."""

    @pytest.fixture
    def function(self):
        """Create EditFileFunction instance."""
        return EditFileFunction()

    async def test_edit_file_success_from_yaml(
        self, hass, function, exposed_entities, llm_context, tmp_path
    ):
        """Test editing a file successfully from yaml definition."""
        workdir = tmp_path / "extended_openai_conversation_responses"
        test_file = workdir / "edit_test.txt"
        test_file.write_text("Hello World\nThis is a test.")

        function_tool = prepare_function_tool_from_yaml("edit_file_example.yaml", 0)
        function_config = function_tool["function"]

        arguments = {
            "filename": "edit_test.txt",
            "old_text": "Hello World",
            "new_text": "Goodbye World",
        }

        result = await function.execute(
            hass, function_config, arguments, llm_context, exposed_entities
        )

        assert result["success"] is True
        assert "path" in result
        assert result["replacements"] == 1

        new_content = test_file.read_text()
        assert "Goodbye World" in new_content
        assert "Hello World" not in new_content
        assert "This is a test." in new_content

    async def test_edit_file_multiline_replacement_from_yaml(
        self, hass, function, exposed_entities, llm_context, tmp_path
    ):
        """Test editing multiple lines from yaml."""
        workdir = tmp_path / "extended_openai_conversation_responses"
        test_file = workdir / "multiline.txt"
        test_file.write_text("Line 1\nLine 2\nLine 3\nLine 4")

        function_tool = prepare_function_tool_from_yaml("edit_file_example.yaml", 0)
        function_config = function_tool["function"]

        arguments = {
            "filename": "multiline.txt",
            "old_text": "Line 2\nLine 3",
            "new_text": "New Line 2\nNew Line 3",
        }

        result = await function.execute(
            hass, function_config, arguments, llm_context, exposed_entities
        )

        assert result["success"] is True
        new_content = test_file.read_text()
        assert "New Line 2\nNew Line 3" in new_content
        assert "Line 2\nLine 3" not in new_content

    async def test_edit_file_not_found(
        self, hass, function, exposed_entities, llm_context
    ):
        """Test editing a file that doesn't exist."""
        function_tool = prepare_function_tool_from_yaml("edit_file_example.yaml", 0)
        function_config = function_tool["function"]

        arguments = {
            "filename": "nonexistent.txt",
            "old_text": "old",
            "new_text": "new",
        }

        result = await function.execute(
            hass, function_config, arguments, llm_context, exposed_entities
        )

        assert "error" in result
        assert "not found" in result["error"].lower()

    async def test_edit_file_text_not_found(
        self, hass, function, exposed_entities, llm_context, tmp_path
    ):
        """Test editing when old_text doesn't exist in file."""
        workdir = tmp_path / "extended_openai_conversation_responses"
        test_file = workdir / "test.txt"
        test_file.write_text("Some content without the searched text")

        function_tool = prepare_function_tool_from_yaml("edit_file_example.yaml", 0)
        function_config = function_tool["function"]

        arguments = {
            "filename": "test.txt",
            "old_text": "Not in file",
            "new_text": "new text",
        }

        result = await function.execute(
            hass, function_config, arguments, llm_context, exposed_entities
        )

        assert "error" in result
        assert "not found in file" in result["error"].lower()

    async def test_edit_file_multiple_occurrences(
        self, hass, function, exposed_entities, llm_context, tmp_path
    ):
        """Test that multiple occurrences are rejected."""
        workdir = tmp_path / "extended_openai_conversation_responses"
        test_file = workdir / "duplicate.txt"
        test_file.write_text("Hello\nHello\nGoodbye")

        function_tool = prepare_function_tool_from_yaml("edit_file_example.yaml", 0)
        function_config = function_tool["function"]

        arguments = {
            "filename": "duplicate.txt",
            "old_text": "Hello",
            "new_text": "Hi",
        }

        result = await function.execute(
            hass, function_config, arguments, llm_context, exposed_entities
        )

        assert "error" in result
        assert "2 times" in result["error"]
        assert test_file.read_text() == "Hello\nHello\nGoodbye"

    async def test_edit_file_is_directory(
        self, hass, function, exposed_entities, llm_context, tmp_path
    ):
        """Test editing a directory instead of file."""
        workdir = tmp_path / "extended_openai_conversation_responses"
        test_dir = workdir / "testdir"
        test_dir.mkdir()

        function_tool = prepare_function_tool_from_yaml("edit_file_example.yaml", 0)
        function_config = function_tool["function"]

        arguments = {
            "filename": "testdir",
            "old_text": "old",
            "new_text": "new",
        }

        result = await function.execute(
            hass, function_config, arguments, llm_context, exposed_entities
        )

        assert "error" in result
        assert "not a file" in result["error"].lower()

    async def test_edit_file_outside_allowed_dir(
        self, hass, function, exposed_entities, llm_context, tmp_path
    ):
        """Test that editing files outside allowed directories is blocked."""
        external_file = tmp_path / "external.txt"
        external_file.write_text("External content")

        function_tool = prepare_function_tool_from_yaml("edit_file_example.yaml", 0)
        function_config = function_tool["function"]

        arguments = {
            "filename": str(external_file),
            "old_text": "External",
            "new_text": "Modified",
        }

        result = await function.execute(
            hass, function_config, arguments, llm_context, exposed_entities
        )

        assert "error" in result
        assert (
            "access denied" in result["error"].lower()
            or "not in allowed" in result["error"].lower()
        )
        assert external_file.read_text() == "External content"

    async def test_edit_file_with_custom_allow_dir_from_yaml(
        self, hass, function, exposed_entities, llm_context
    ):
        """Test editing file in custom allowed directory from yaml."""
        import os

        # Use /tmp directory as specified in yaml
        test_file_path = os.path.join("/tmp", "custom_edit_test.txt")

        with open(test_file_path, "w") as f:
            f.write("Original text")

        try:
            function_tool = prepare_function_tool_from_yaml("edit_file_example.yaml", 1)
            function_config = function_tool["function"]

            arguments = {
                "filepath": test_file_path,
                "old_text": "Original",
                "new_text": "Modified",
            }

            result = await function.execute(
                hass, function_config, arguments, llm_context, exposed_entities
            )

            assert result["success"] is True
            with open(test_file_path) as f:
                assert f.read() == "Modified text"
        finally:
            # Clean up
            if os.path.exists(test_file_path):
                os.remove(test_file_path)

    async def test_edit_file_template_path(
        self, hass, function, exposed_entities, llm_context, tmp_path
    ):
        """Test editing file with templated path."""
        workdir = tmp_path / "extended_openai_conversation_responses"
        test_file = workdir / "templated.txt"
        test_file.write_text("Template test")

        function_tool = prepare_function_tool_from_yaml("edit_file_example.yaml", 0)
        function_config = function_tool["function"]

        arguments = {
            "filename": "templated.txt",
            "old_text": "Template",
            "new_text": "Replaced",
        }

        result = await function.execute(
            hass, function_config, arguments, llm_context, exposed_entities
        )

        assert result["success"] is True
        assert test_file.read_text() == "Replaced test"

    async def test_edit_file_unicode_content_from_yaml(
        self, hass, function, exposed_entities, llm_context, tmp_path
    ):
        """Test editing file with unicode content from yaml."""
        workdir = tmp_path / "extended_openai_conversation_responses"
        test_file = workdir / "unicode.txt"
        test_file.write_text("Hello 世界", encoding="utf-8")

        function_tool = prepare_function_tool_from_yaml("edit_file_example.yaml", 2)
        function_config = function_tool["function"]

        arguments = {
            "filename": "unicode.txt",
            "old_text": "世界",
            "new_text": "World",
        }

        result = await function.execute(
            hass, function_config, arguments, llm_context, exposed_entities
        )

        assert result["success"] is True
        assert test_file.read_text(encoding="utf-8") == "Hello World"

    async def test_edit_file_preserves_rest_of_content(
        self, hass, function, exposed_entities, llm_context, tmp_path
    ):
        """Test that edit preserves content before and after replacement."""
        workdir = tmp_path / "extended_openai_conversation_responses"
        test_file = workdir / "preserve.txt"
        test_file.write_text("Start\nMiddle line to replace\nEnd")

        function_tool = prepare_function_tool_from_yaml("edit_file_example.yaml", 0)
        function_config = function_tool["function"]

        arguments = {
            "filename": "preserve.txt",
            "old_text": "Middle line to replace",
            "new_text": "New middle line",
        }

        result = await function.execute(
            hass, function_config, arguments, llm_context, exposed_entities
        )

        assert result["success"] is True
        new_content = test_file.read_text()
        assert "Start" in new_content
        assert "New middle line" in new_content
        assert "End" in new_content
        assert "Middle line to replace" not in new_content

    async def test_get_function(self):
        """Test getting edit_file function from registry."""
        function = get_function("edit_file")
        assert isinstance(function, EditFileFunction)
