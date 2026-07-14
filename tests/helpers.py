"""Test helper functions."""

from pathlib import Path
from typing import Any

import yaml


def load_function_tool_yaml(filename: str) -> list[dict[str, Any]]:
    """Load function definition from yaml file.

    Args:
        filename: Name of yaml file in tests/fixtures/functions/

    Returns:
        List of function definitions with spec and function keys
    """
    fixtures_dir = Path(__file__).parent / "fixtures" / "functions"
    yaml_path = fixtures_dir / filename

    with open(yaml_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_function_tool_from_yaml(filename: str, index: int = 0) -> dict[str, Any]:
    """Get a single function definition from yaml file.

    NOTE: This returns raw YAML data without validation. For most tests,
    use prepare_function_tool_from_yaml() instead, which validates the
    function config (converting strings to Template objects).

    Args:
        filename: Name of yaml file in tests/fixtures/functions/
        index: Index of function in yaml file (default: 0)

    Returns:
        Function definition with spec and function keys (unvalidated)
    """
    function_tools = load_function_tool_yaml(filename)
    return function_tools[index]


def prepare_function_tool_from_yaml(filename: str, index: int = 0) -> dict[str, Any]:
    """Get a function definition from yaml file with validated schema.

    This mimics production behavior in conversation.py._get_function_tools()
    by validating the function config and converting string templates to
    Template objects.

    Args:
        filename: Name of yaml file in tests/fixtures/functions/
        index: Index of function in yaml file (default: 0)

    Returns:
        Function definition with validated function config containing Template objects
    """
    from custom_components.extended_openai_conversation_responses.functions import (
        get_function,
    )

    function_tool = get_function_tool_from_yaml(filename, index)

    # Validate and convert templates (mirrors conversation.py:237)
    if isinstance(function_tool, dict) and "function" in function_tool:
        function_config = function_tool["function"]
        if isinstance(function_config, dict) and "type" in function_config:
            function = get_function(function_config["type"])
            function_tool["function"] = function.validate_schema(function_config)

    return function_tool
