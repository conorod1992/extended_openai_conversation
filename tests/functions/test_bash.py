"""Tests for BashFunction using yaml definitions."""

import pytest

from custom_components.extended_openai_conversation_responses.functions import (
    BashFunction,
    get_function,
)
from tests.helpers import prepare_function_tool_from_yaml


class TestBashFunctionYaml:
    """Test BashFunction using yaml definitions."""

    @pytest.fixture
    def function(self):
        """Create BashFunction instance."""
        return BashFunction()

    async def test_list_directory_from_yaml(
        self, hass, function, exposed_entities, llm_context
    ):
        """Test list_directory command from yaml definition."""
        function_tool = prepare_function_tool_from_yaml("bash_example.yaml", 0)
        function_config = function_tool["function"]

        arguments = {}

        result = await function.execute(
            hass, function_config, arguments, llm_context, exposed_entities
        )

        assert result["exit_code"] == 0
        assert result["stdout"]  # Should have some directory listing output

    async def test_custom_directory_command_from_yaml(
        self, hass, function, exposed_entities, llm_context, tmp_path
    ):
        """Test custom_directory_command with cwd parameter from yaml."""
        # Create test file
        test_dir = tmp_path / "bash_test"
        test_dir.mkdir()
        test_file = test_dir / "test.txt"
        test_file.write_text("test content")

        function_tool = prepare_function_tool_from_yaml("bash_example.yaml", 9)
        function_config = function_tool["function"]

        arguments = {"directory": str(test_dir), "filename": "test.txt"}

        result = await function.execute(
            hass, function_config, arguments, llm_context, exposed_entities
        )

        assert result["exit_code"] == 0
        assert "test content" in result["stdout"]

    async def test_print_working_directory_from_yaml(
        self, hass, function, exposed_entities, llm_context
    ):
        """Test print_working_directory command from yaml."""
        function_tool = prepare_function_tool_from_yaml("bash_example.yaml", 3)
        function_config = function_tool["function"]

        arguments = {}

        result = await function.execute(
            hass, function_config, arguments, llm_context, exposed_entities
        )

        assert result["exit_code"] == 0
        assert result["stdout"]  # Should have current directory path

    async def test_deny_patterns(self, hass, function, exposed_entities, llm_context):
        """Test that deny patterns block commands."""
        dangerous_commands = [
            "rm -rf /",
            "dd if=/dev/zero of=/dev/sda",
            "mkfs.ext4 /dev/sda1",
            "> /etc/passwd",
        ]

        for cmd in dangerous_commands:
            from homeassistant.helpers.template import Template

            function_config = {
                "type": "bash",
                "allow_unsafe_shell": True,
                "command": Template(cmd, hass),
            }
            arguments = {}

            result = await function.execute(
                hass, function_config, arguments, llm_context, exposed_entities
            )

            assert "error" in result
            assert "blocked" in result["error"].lower()

    async def test_path_traversal_protection(
        self, hass, function, exposed_entities, llm_context
    ):
        """Test that path traversal is blocked."""
        from homeassistant.helpers.template import Template

        function_config = {
            "type": "bash",
            "allow_unsafe_shell": True,
            "command": Template("cat ../../../etc/passwd", hass),
        }
        arguments = {}

        result = await function.execute(
            hass, function_config, arguments, llm_context, exposed_entities
        )

        assert "error" in result
        assert "traversal" in result["error"].lower()

    async def test_list_root_directory_with_description_from_yaml(
        self, hass, function, exposed_entities, llm_context
    ):
        """Test list_root_directory with restrict_to_workspace in description from yaml."""
        function_tool = prepare_function_tool_from_yaml("bash_example.yaml", 1)
        function_config = function_tool["function"]

        arguments = {}

        result = await function.execute(
            hass, function_config, arguments, llm_context, exposed_entities
        )

        # Should be blocked by workspace restriction (no explicit restrict_to_workspace: false)
        assert "error" in result

    async def test_restrict_to_workspace_false(
        self, hass, function, exposed_entities, llm_context
    ):
        """Test that restrict_to_workspace=false allows commands outside workspace."""
        function_tool = prepare_function_tool_from_yaml("bash_example.yaml", 2)
        function_config = function_tool["function"]

        arguments = {}

        result = await function.execute(
            hass, function_config, arguments, llm_context, exposed_entities
        )

        # Should not have error about workspace restriction
        # Note: Command tries to access /root which may fail due to permissions,
        # but should not fail due to workspace restriction
        assert (
            "error" not in result or "workspace" not in result.get("error", "").lower()
        )

    async def test_allow_patterns_blocks_unlisted_from_yaml(
        self, hass, function, exposed_entities, llm_context
    ):
        """Test that allow patterns block unlisted commands from yaml."""
        function_tool = prepare_function_tool_from_yaml("bash_example.yaml", 4)
        function_config = function_tool["function"]

        arguments = {}

        result = await function.execute(
            hass, function_config, arguments, llm_context, exposed_entities
        )

        assert "error" in result
        assert "not in allowlist" in result["error"]

    async def test_output_truncation_from_yaml(
        self, hass, function, exposed_entities, llm_context
    ):
        """Test that long output is truncated from yaml."""
        function_tool = prepare_function_tool_from_yaml("bash_example.yaml", 10)
        function_config = function_tool["function"]

        arguments = {}

        result = await function.execute(
            hass, function_config, arguments, llm_context, exposed_entities
        )

        assert result["exit_code"] == 0
        assert "truncated" in result["stdout"].lower()
        assert len(result["stdout"]) < 12000  # SHELL_OUTPUT_LIMIT is 10000

    async def test_rm_rf_blocked_from_yaml(
        self, hass, function, exposed_entities, llm_context
    ):
        """Test that rm -rf command is blocked from yaml."""
        function_tool = prepare_function_tool_from_yaml("bash_example.yaml", 5)
        function_config = function_tool["function"]

        arguments = {}

        result = await function.execute(
            hass, function_config, arguments, llm_context, exposed_entities
        )

        assert "error" in result
        assert "blocked" in result["error"].lower()

    async def test_disk_wipe_blocked_from_yaml(
        self, hass, function, exposed_entities, llm_context
    ):
        """Test that dd disk wipe command is blocked from yaml."""
        function_tool = prepare_function_tool_from_yaml("bash_example.yaml", 7)
        function_config = function_tool["function"]

        arguments = {}

        result = await function.execute(
            hass, function_config, arguments, llm_context, exposed_entities
        )

        assert "error" in result
        assert "blocked" in result["error"].lower()

    async def test_path_traversal_attack_blocked_from_yaml(
        self, hass, function, exposed_entities, llm_context
    ):
        """Test that path traversal attack is blocked from yaml."""
        function_tool = prepare_function_tool_from_yaml("bash_example.yaml", 8)
        function_config = function_tool["function"]

        arguments = {}

        result = await function.execute(
            hass, function_config, arguments, llm_context, exposed_entities
        )

        assert "error" in result
        assert "traversal" in result["error"].lower()

    async def test_get_function(self):
        """Test getting bash tool from registry."""
        tool = get_function("bash")
        assert isinstance(tool, BashFunction)
