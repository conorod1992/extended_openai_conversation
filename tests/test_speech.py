"""Tests for TTS-only assistant response processing."""

import logging

from custom_components.extended_openai_conversation_responses.speech import (
    process_speech_text,
)


def _config(**updates):
    return {
        "speech_processing_enabled": True,
        "speech_strip_markdown": True,
        "speech_strip_urls": True,
        "speech_regex_replacements": [],
        **updates,
    }


def test_markdown_citation_and_bare_urls_are_removed_without_mutating_original() -> (
    None
):
    original = "Inside Iran ([apnews.com](https://apnews.com/article/example)) See https://example.com/news."
    spoken = process_speech_text(original, _config())
    assert spoken == "Inside Iran See"
    assert "apnews.com" in original
    assert "https://example.com" in original


def test_common_markdown_is_simplified_conservatively() -> None:
    original = "## Update\n- **Heating** is `on`\n- Temperature is 20 C"
    assert (
        process_speech_text(original, _config())
        == "Update\nHeating is on\nTemperature is 20 C"
    )


def test_custom_replacements_run_in_order_and_allow_empty_replacement() -> None:
    config = _config(
        speech_regex_replacements=[
            {"pattern": r"\[\d+\]", "replacement": ""},
            {"pattern": r"\bHA\b", "replacement": "Home Assistant"},
            {"pattern": "Home Assistant", "replacement": "the smart home"},
        ]
    )
    assert process_speech_text("HA [12] is ready", config) == "the smart home is ready"


def test_runtime_invalid_regex_is_skipped_safely(caplog) -> None:
    config = _config(
        speech_strip_markdown=False,
        speech_strip_urls=False,
        speech_regex_replacements=[
            {"pattern": "[", "replacement": ""},
            {"pattern": "HA", "replacement": "Home Assistant"},
        ],
    )
    with caplog.at_level(logging.WARNING):
        assert process_speech_text("HA works", config) == "Home Assistant works"
    assert "Skipping invalid speech regex replacement" in caplog.text


def test_disabled_processing_and_ordinary_text_are_unchanged() -> None:
    text = "Ordinary spoken text."
    assert process_speech_text(text, {"speech_processing_enabled": False}) == text
    assert process_speech_text(text, _config()) == text
