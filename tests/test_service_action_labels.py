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


def test_broadcast_and_quiet_hours_translations_match_targeted_service_fields():
    """Older locale omissions must not expand this three-service contract."""
    services = yaml.safe_load((ROOT / "services.yaml").read_text(encoding="utf-8"))
    targeted = ("broadcast", "enable_quiet_hours", "disable_quiet_hours")
    for path in [ROOT / "strings.json", *sorted((ROOT / "translations").glob("*.json"))]:
        translated = json.loads(path.read_text(encoding="utf-8"))["services"]
        for service_id in targeted:
            expected = services[service_id]
            actual = translated[service_id]
            assert actual["name"] and actual["description"], (path.name, service_id)
            assert set(actual.get("fields", {})) == set(expected.get("fields", {}))
            for field_id, field in actual.get("fields", {}).items():
                assert field["name"] and field["description"], (path.name, field_id)
            if path.name in {"strings.json", "en.json"}:
                assert actual["name"] == expected["name"]
                assert actual["description"] == expected["description"]
                for field_id, field in actual.get("fields", {}).items():
                    assert field == {key: expected["fields"][field_id][key] for key in ("name", "description")}
    assert len(services["broadcast"]["fields"]) == 8

def test_rebuild_memory_action_has_matching_english_metadata():
    action = "rebuild_memory_embeddings"
    services = yaml.safe_load((ROOT / "services.yaml").read_text(encoding="utf-8"))
    expected = {key: services[action][key] for key in ("name", "description")}
    expected["fields"] = {
        name: {key: field[key] for key in ("name", "description")}
        for name, field in services[action]["fields"].items()
    }
    for path in (ROOT / "strings.json", ROOT / "translations/en.json"):
        actual = json.loads(path.read_text(encoding="utf-8"))["services"][action]
        assert actual == expected
