"""Maintained Skill-loader pagination, including previously saved presets."""

from copy import deepcopy
from typing import Any

SKILL_PAGE_OFFSET_TEMPLATE = (
    "{{ offset if offset is defined and offset is not none else 0 }}"
)
SKILL_OFFSET_SCHEMA = {
    "type": ["integer", "null"],
    "description": "Non-negative Unicode character offset. Start at 0; when has_more is true, call again with next_offset until the file is complete.",
}


def upgrade_skill_loader_paging(tool: dict[str, Any]) -> dict[str, Any]:
    """Extend old maintained loaders in memory without rewriting authored data."""
    spec, function = tool.get("spec", {}), tool.get("function", {})
    parameters = spec.get("parameters", {})
    if not (
        spec.get("name") == "load_skill"
        and function.get("type") == "read_file"
        and getattr(function.get("path"), "template", function.get("path"))
        == "{{extended_openai.skill_dir(name)}}/{{file}}"
        and parameters.get("type") == "object"
        and isinstance(parameters.get("properties"), dict)
        and set(parameters["properties"]) == {"name", "file"}
        and all(
            isinstance(parameters["properties"][key], dict)
            and parameters["properties"][key].get("type") == "string"
            for key in ("name", "file")
        )
        and isinstance(parameters.get("required"), list)
        and set(parameters["required"]) == {"name", "file"}
        and "page_offset" not in function
    ):
        return tool
    upgraded = deepcopy(tool)
    upgraded["spec"]["parameters"]["properties"]["offset"] = deepcopy(
        SKILL_OFFSET_SCHEMA
    )
    if spec.get("strict") is True:
        upgraded["spec"]["parameters"]["required"] = [
            *upgraded["spec"]["parameters"].get("required", []),
            "offset",
        ]
    upgraded["function"]["page_offset"] = SKILL_PAGE_OFFSET_TEMPLATE
    return upgraded
