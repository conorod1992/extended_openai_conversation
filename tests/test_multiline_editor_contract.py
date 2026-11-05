"""Accepted multiline values retain their intended runtime behavior."""

from custom_components.extended_openai_conversation_responses.agent_config import (
    validate_speech_regex_replacements,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    RequestRules,
    validate_rule,
)
from custom_components.extended_openai_conversation_responses.speech import (
    process_speech_text,
)


async def test_multiline_phrase_is_one_equals_command_and_wording_remains_separate():
    from tests.test_request_rules import MemoryStore

    manager = RequestRules(MemoryStore())
    await manager.async_initialize()
    authored = [
        validate_rule(
            {
                "id": "multiline",
                "name": "Multiline",
                "enabled": True,
                "phrases": ["turn off\nthe bedroom lamp"],
                "match_type": "equals",
                "action_type": "local_action",
                "action": {
                    "actions": [
                        {
                            "action": "light.turn_off",
                            "target": {"entity_id": "light.bedroom_lamp"},
                        }
                    ],
                    "continue_to_ai": False,
                    "success_response": "Off.\nGood night.",
                    "failure_response": "Failed.\nTry again.",
                },
            }
        )
    ]
    await manager.async_create(authored[0])
    wording = [{"canonical": "turn off", "alternatives": ["switch\noff"]}]
    await manager.async_set_wording_groups(wording)
    assert manager.match("turn off the bedroom lamp") is not None
    assert manager.match("switch off the bedroom lamp") is not None
    assert manager.match("turn off") is None
    assert manager.match("the bedroom lamp") is None


def test_verbose_comment_and_multiline_replacement_survive_order_change():
    rules = validate_speech_regex_replacements(
        [
            {
                "pattern": "(?x)\n\\bHA\\b # abbreviation\n| \\bHASS\\b",
                "replacement": "Home\nAssistant",
            },
            {"pattern": "XYZ", "replacement": "Example"},
        ]
    )
    config = {
        "speech_processing_enabled": True,
        "speech_strip_markdown": False,
        "speech_strip_urls": False,
        "speech_regex_replacements": rules,
    }
    before = process_speech_text("HA and HASS", config)
    assert before == "Home\nAssistant and Home\nAssistant"
    config["speech_regex_replacements"] = list(reversed(rules))
    assert process_speech_text("HA and HASS", config) == before
