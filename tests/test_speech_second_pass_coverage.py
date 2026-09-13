"""Second-pass residual coverage for speech sanitization edge cases."""

from __future__ import annotations

from custom_components.extended_openai_conversation_responses import speech


def _config(**updates):
    return {
        "speech_processing_enabled": True,
        "speech_strip_markdown": True,
        "speech_strip_urls": True,
        "speech_regex_replacements": [],
        **updates,
    }


def test_suppress_trims_trailing_space_without_dropping_nonempty_chunk() -> None:
    sanitizer = speech.StreamingSpeechSanitizer()
    output = ["text "]
    sanitizer._last_output = "text "

    assert sanitizer._suppress(output, 7) == 7
    assert output == ["text"]
    assert sanitizer._last_output == "text"


def test_lone_markdown_image_marker_waits_for_stream_completion() -> None:
    sanitizer = speech.StreamingSpeechSanitizer(markdown=True, urls=False)

    assert sanitizer.feed("Hello !") == "Hello "
    assert sanitizer.finish() == "!"


def test_malformed_markdown_construct_is_released_at_buffer_limit() -> None:
    sanitizer = speech.StreamingSpeechSanitizer(
        markdown=True,
        urls=False,
        max_buffer_chars=32,
    )
    malformed = "[" + ("x" * 32)

    assert sanitizer.feed(malformed) == malformed
    assert sanitizer.buffered_chars == 0
    assert sanitizer.finish() == ""


def test_format_releases_ambiguous_line_prefix_at_safety_limit() -> None:
    sanitizer = speech.StreamingSpeechSanitizer(markdown=True, urls=False)
    text = " " * (speech._FORMAT_PREFIX_LIMIT + 1)

    assert sanitizer._format(text, final=False) == text
    assert sanitizer._format_buffer == ""
    assert sanitizer._line_start is False


def test_line_prefix_finalizes_ambiguous_terminal_markers_as_plain_text() -> None:
    line_prefix = speech.StreamingSpeechSanitizer._line_prefix

    assert line_prefix("#", True) == ("none", 0)
    assert line_prefix("-", True) == ("none", 0)
    assert line_prefix("12", True) == ("none", 0)
    assert line_prefix("12)", True) == ("none", 0)
    assert line_prefix("   ", True) == ("none", 0)


def test_streaming_processing_requires_at_least_one_builtin_cleanup() -> None:
    assert (
        speech.streaming_speech_processing_enabled(
            _config(speech_strip_markdown=False, speech_strip_urls=False)
        )
        is False
    )


def test_non_list_regex_configuration_is_ignored_safely() -> None:
    config = _config(
        speech_strip_markdown=False,
        speech_strip_urls=False,
        speech_regex_replacements={"pattern": "hello", "replacement": "goodbye"},
    )

    assert speech.process_speech_text("hello   world", config) == "hello world"
