"""Focused residual coverage for streaming speech sanitization."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses import speech


def _config(**updates):
    return {
        "speech_processing_enabled": True,
        "speech_strip_markdown": True,
        "speech_strip_urls": True,
        "speech_regex_replacements": [],
        **updates,
    }


def _stream(chunks: list[str], **kwargs) -> str:
    sanitizer = speech.StreamingSpeechSanitizer(**kwargs)
    return "".join([*(sanitizer.feed(chunk) for chunk in chunks), sanitizer.finish()])


def test_emit_separator_handles_whitespace_punctuation_and_plain_text() -> None:
    sanitizer = speech.StreamingSpeechSanitizer()
    output: list[str] = []

    sanitizer._emit(output, "")
    assert output == []

    sanitizer._last_output = "word"
    sanitizer._needs_separator = True
    sanitizer._emit(output, "   ")
    assert output == []
    assert sanitizer._needs_separator is True

    sanitizer._emit(output, ",")
    assert output == [","]
    assert sanitizer._needs_separator is False

    sanitizer._last_output = "word"
    sanitizer._needs_separator = True
    output.clear()
    sanitizer._emit(output, "next")
    assert output == [" ", "next"]


def test_suppress_and_hold_whitespace_cover_trailing_chunks() -> None:
    sanitizer = speech.StreamingSpeechSanitizer()
    output = ["text", "   "]
    sanitizer._last_output = "text   "

    end = sanitizer._suppress(output, 12)

    assert end == 12
    assert output == ["text"]
    assert sanitizer._last_output == "text"
    assert sanitizer._needs_separator is True
    assert sanitizer.suppressed_constructs == 1

    output = ["x", " ", "\t"]
    sanitizer._last_output = "x \t"
    assert sanitizer._hold_preceding_whitespace(output, 5) == 3
    assert output == ["x"]
    assert sanitizer._last_output == "x"


def test_outer_image_link_and_partial_outer_parenthesis_paths() -> None:
    assert _stream(["Before (![alt](https://example.com/a.png)) after"]) == "Before after"

    sanitizer = speech.StreamingSpeechSanitizer()
    assert sanitizer.feed("Before (") == "Before"
    assert sanitizer.finish() == " ("


def test_partial_url_prefix_and_identifier_paths() -> None:
    sanitizer = speech.StreamingSpeechSanitizer(markdown=False, urls=True)
    assert sanitizer.feed("prefix htt") == "prefix"
    assert sanitizer.feed("x next") == " httx next"
    assert sanitizer.finish() == ""

    assert _stream(["h", "ttp://example.com next"], markdown=False, urls=True) == "next"


def test_markdown_link_parser_final_and_nested_failure_paths() -> None:
    parser = speech.StreamingSpeechSanitizer._markdown_link_end

    assert parser("[label", 0, True) == ("no", 0)
    assert parser("[label]", 0, True) == ("no", 0)
    assert parser("[label](", 0, True) == ("no", 0)
    assert parser("[label]x", 0, True) == ("no", 0)
    assert parser("[label](bad url)", 0, False) == ("no", 0)
    assert parser("[label](https://example.com/a(b)c)", 0, False)[0] == "complete"


def test_format_handles_newline_backticks_markers_and_terminal_tail() -> None:
    sanitizer = speech.StreamingSpeechSanitizer(markdown=True, urls=False)
    assert sanitizer._format("plain\n`code` **bold** __word__", final=True) == (
        "plain\ncode bold word"
    )

    sanitizer = speech.StreamingSpeechSanitizer(markdown=True, urls=False)
    assert sanitizer._format("trailing*", final=False) == "trailing"
    assert sanitizer._format("", final=True) == "*"


def test_line_prefix_strips_supported_markers_and_rejects_near_misses() -> None:
    line_prefix = speech.StreamingSpeechSanitizer._line_prefix

    assert line_prefix("   ###   Heading", False) == ("strip", 9)
    assert line_prefix("\t>  Quote", False) == ("strip", 4)
    assert line_prefix("  -   Item", False) == ("strip", 6)
    assert line_prefix("12)  Item", False) == ("strip", 5)
    assert line_prefix("123x Item", False) == ("none", 0)
    assert line_prefix("####### Heading", False) == ("none", 0)
    assert line_prefix("-not-a-list", False) == ("none", 0)


def test_delta_listener_drops_content_only_suppressed_delta_and_flushes_tail() -> None:
    heard: list[dict] = []
    listener = speech._SpeechDeltaListener(
        lambda _chat_log, delta: heard.append(delta), _config()
    )
    chat_log = object()

    listener(chat_log, {"content": "[source](https://example.com)"})
    assert heard == []

    listener(chat_log, {"content": "tail "})
    listener.flush(chat_log)
    assert "".join(item.get("content", "") for item in heard) == "tail "


def test_streaming_cleanup_flush_failure_still_restores_original_listener(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = lambda _chat_log, _delta: None
    chat_log = SimpleNamespace(delta_listener=original)

    def fail_flush(self, _chat_log):
        raise RuntimeError("flush failed")

    monkeypatch.setattr(speech._SpeechDeltaListener, "flush", fail_flush)

    with pytest.raises(RuntimeError, match="flush failed"):
        with speech.async_streaming_speech_cleanup(chat_log, _config()):
            assert chat_log.delta_listener is not original

    assert chat_log.delta_listener is original


def test_streaming_cleanup_summary_runs_after_success(monkeypatch: pytest.MonkeyPatch) -> None:
    original = lambda _chat_log, _delta: None
    chat_log = SimpleNamespace(delta_listener=original)
    calls: list[str] = []

    monkeypatch.setattr(
        speech._SpeechDeltaListener,
        "log_summary",
        lambda self: calls.append("summary"),
    )

    with speech.async_streaming_speech_cleanup(chat_log, _config()):
        assert chat_log.delta_listener is not original

    assert calls == ["summary"]
    assert chat_log.delta_listener is original


def test_process_speech_text_skips_bad_rule_types_and_bad_replacement(caplog) -> None:
    config = _config(
        speech_strip_markdown=False,
        speech_strip_urls=False,
        speech_regex_replacements=[
            None,
            {"replacement": "missing pattern"},
            {"pattern": "x", "replacement": r"\9"},
            {"pattern": "x", "replacement": "y"},
        ],
    )

    with caplog.at_level(logging.WARNING):
        assert speech.process_speech_text("x", config) == "y"

    assert caplog.text.count("Skipping invalid speech regex replacement") == 3


def test_final_whitespace_cleanup_collapses_empty_parens_and_spacing() -> None:
    assert speech._final_whitespace_cleanup("Hello ( )   , world\n\n\nnext") == (
        "Hello, world\nnext"
    )
