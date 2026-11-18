"""Literal escapes and conservative equals headings share the streaming parser."""

from string import punctuation

import pytest

from custom_components.extended_openai_conversation_responses.speech import (
    StreamingSpeechSanitizer,
    _final_whitespace_cleanup,
    process_speech_text,
)
from tests.test_speech import _config
from tests.test_speech_scope_regressions import all_splits, streamed


@pytest.mark.parametrize("mark", punctuation)
def test_each_ascii_punctuation_escape_is_literal_at_every_split(mark):
    source = "Before \\" + mark + " after"
    expected = "Before " + mark + " after"
    assert process_speech_text(source, _config()) == expected
    for split in range(len(source) + 1):
        sanitizer = StreamingSpeechSanitizer()
        assert sanitizer.feed(source[:split]) + sanitizer.feed(source[split:]) + sanitizer.finish() == expected


@pytest.mark.parametrize("slashes", range(1, 8))
def test_escape_parity_does_not_reparse_literal_markers(slashes):
    prefix = "\\" * slashes
    source = prefix + "*word" + prefix + "*"
    literal_slashes = "\\" * (slashes // 2)
    expected = literal_slashes + (
        "*word" + literal_slashes + "*" if slashes % 2 else "word" + literal_slashes
    )
    all_splits(source, expected)


@pytest.mark.parametrize(
    "source,expected",
    [
        (r"\# Heading", "# Heading"),
        (r"\- Item", "- Item"),
        (r"1\. Item", "1. Item"),
        (r"\[name](target)", "[name](target)"),
        (r"\`code\` and **bold**", "`code` and bold"),
        (r"`\*literal\* \q`", r"\*literal\* \q"),
        ("```python\n" + r"\*literal\*" + "\n```", r"\*literal\*"),
        (r"\q C:\Users\name", r"\q C:\Users\name"),
        ("Tail\\", "Tail\\"),
        ("Tail\\\\", "Tail\\"),
        ("Heading\n===\nBody", "Heading\nBody"),
        ("A heading\n  = \t\nBody", "A heading\nBody"),
        ("Heading\n===", "Heading"),
        ("===", "==="),
        ("\n===\n", "==="),
        ("Title\n= = =\nBody", "Title\n= = =\nBody"),
        ("Title\n== operator\nBody", "Title\n== operator\nBody"),
        ("Title\n    ===\nBody", "Title\n ===\nBody"),
        ("Title\n\\===\nBody", "Title\n===\nBody"),
        ("`Title\n===`", "Title\n==="),
        ("```text\nTitle\n===\n```", "Title\n==="),
        ("# Heading\n===", "Heading\n==="),
        ("- Item\n===", "Item\n==="),
        ("[label](target)\n===", "label\n==="),
    ],
)
def test_literal_and_heading_contexts_at_every_split(source, expected):
    all_splits(source, expected)


@pytest.mark.parametrize("markdown", [False, True])
@pytest.mark.parametrize("urls", [False, True])
def test_cleanup_settings_remain_independent(markdown, urls):
    source = r"\*literal\* https://example.com" + "\nTitle\n==="
    expected = "*literal*" if markdown else r"\*literal\*"
    if not urls:
        expected += " https://example.com"
    expected += (" Title" if urls else "\nTitle") + ("" if markdown else "\n===")
    all_splits(source, expected, markdown=markdown, urls=urls)


def test_trailing_backslash_is_held_and_normal_prose_progresses():
    sanitizer = StreamingSpeechSanitizer()
    assert sanitizer.feed("Plain prose \\") == "Plain prose "
    assert sanitizer.buffered_chars == 1
    assert sanitizer.feed("*literal") == "*literal"
    assert sanitizer.finish() == ""
    assert sanitizer.max_buffered_chars < 32


def test_long_completed_indexes_and_streams_preserve_code_and_escape_order():
    prefix = "Plain words. " * 400
    suffix = r"\*literal\* [label](target) `\*code\*`" + "\nHeading\n===\nEnd\\"
    source = prefix + suffix
    expected = prefix + "*literal* label " + r"\*code\*" + "\nHeading\nEnd\\"
    assert (
        _final_whitespace_cleanup(StreamingSpeechSanitizer().process_complete(source))
        == expected
    )
    for width in (1, 7, 31, 4096):
        assert streamed(source, width=width) == expected
    for split in range(len(prefix), len(source) + 1):
        sanitizer = StreamingSpeechSanitizer()
        assert (
            _final_whitespace_cleanup(
                sanitizer.feed(source[:split])
                + sanitizer.feed(source[split:])
                + sanitizer.finish()
            )
            == expected
        )
    config = _config(
        speech_regex_replacements=[
            {"pattern": r"\*literal\*", "replacement": "changed"}
        ]
    )
    assert process_speech_text(source, config) == expected.replace(
        "*literal*", "changed"
    )
    assert source.endswith("End\\")


def test_long_equals_runs_are_released_with_bounded_delay():
    source = "Title\n" + "=" * 5000 + "\nEnd"
    sanitizer = StreamingSpeechSanitizer()
    chunks = [sanitizer.feed(char) for char in source]
    assert _final_whitespace_cleanup("".join(chunks) + sanitizer.finish()) == source
    assert sanitizer.max_buffered_chars < 80


@pytest.mark.parametrize("opener", ["[", "**"])
def test_unresolved_backslash_is_held_even_when_malformed_input_overflows(opener):
    sanitizer = StreamingSpeechSanitizer(max_buffer_chars=32)
    prefix = opener + "word " * 10
    released = sanitizer.feed(prefix + "\\")
    assert not released.endswith("\\")
    assert sanitizer.buffered_chars == 1
    tail = sanitizer.feed("!") + sanitizer.finish()
    assert _final_whitespace_cleanup(released + tail) == _final_whitespace_cleanup(
        prefix + "!"
    )
