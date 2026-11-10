"""Service labels must be visible in HA's packaged English translations."""

import json
from pathlib import Path

import yaml


ROOT = Path(__file__).parents[1] / "custom_components" / "extended_openai_conversation_responses"


def test_action_field_names_are_published_in_metadata_and_english_translations() -> None:
    actions = (
        "call_function", "download_skill", "memory_list", "memory_delete",
        "memory_clear", "enable_function_tools", "disable_function_tools",
        "enable_function_groups", "disable_function_groups",
        "guest_mode_update", "guest_mode_disable",
    )
    services = yaml.safe_load((ROOT / "services.yaml").read_text(encoding="utf-8"))
    sources = [
        json.loads((ROOT / "strings.json").read_text(encoding="utf-8")),
        json.loads((ROOT / "translations/en.json").read_text(encoding="utf-8")),
    ]
    for action in actions:
        for field, details in services[action]["fields"].items():
            assert details.get("name"), (action, field)
            for source in sources:
                assert source["services"][action]["fields"][field]["name"], (
                    action, field
                )
