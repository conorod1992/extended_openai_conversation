"""Incremental and indexed completed cleanup for Markdown links and rules."""

from __future__ import annotations

from time import perf_counter

import pytest

from custom_components.extended_openai_conversation_responses.speech import (
    StreamingSpeechSanitizer,
    _final_whitespace_cleanup,
    process_speech_text,
)


def _stream(text: str, split: int | None = None, *, markdown=True, urls=True) -> str:
    sanitizer = StreamingSpeechSanitizer(markdown=markdown, urls=urls)
    chunks = list(text) if split is None else [text[:split], text[split:]]
    result = "".join(sanitizer.feed(chunk) for chunk in chunks) + sanitizer.finish()
    assert sanitizer.buffered_chars == 0
    return _final_whitespace_cleanup(result)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ('Read [Home Assistant](https://example.com "title") next.', "Read Home Assistant next."),
        ("Read [Source](https://example.com 'a simple title') now.", "Read Source now."),
        ("See [Setup](https://example.com (optional title)) first.", "See Setup first."),
        ("Read [Docs](<https://example.com/a(b)c> \"Description\") now.", "Read Docs now."),
        ("Read [example.com](<https://example.com/path> 'Citation').", "Read."),
        ("Read [index.html](<https://other.example/page>).", "Read index.html."),
        ("See ([Source](<https://example.com/path> \"title\")).", "See Source."),
        ("Choose ![thumbnail](<https://example.com/image.png> 'title') now.", "Choose now."),
        (r'Read [Manual](https://example.com/a\)b "a \"quoted\" title").', "Read Manual."),
        ("First\n---\nSecond", "First\nSecond"),
        ("First\n***\nSecond", "First\nSecond"),
        ("First\n___\nSecond", "First\nSecond"),
        ("First\n - - - \nSecond", "First\nSecond"),
        ("---\n# Title\n___", "Title"),
        ("Leave --- inline and a_b__identifier unchanged.", "Leave --- inline and a_b__identifier unchanged."),
        (r"\--- is literal.", r"\--- is literal."),
        ("---text is literal", "---text is literal"),
        ("**bold** and 2 ** 3", "bold and 2 ** 3"),
    ],
)
def test_speech_link_titles_rules_all_splits_and_complete(source, expected):
    config = {
        "speech_processing_enabled": True,
        "speech_strip_markdown": True,
        "speech_strip_urls": True,
        "speech_regex_replacements": [],
    }
    assert process_speech_text(source, config) == expected
    assert _stream(source) == expected
    for split in range(len(source) + 1):
        assert _stream(source, split) == expected, split


@pytest.mark.parametrize("markdown", [False, True])
@pytest.mark.parametrize("urls", [False, True])
def test_cleanup_feature_toggles_do_not_strip_literal_markdown_when_disabled(markdown, urls):
    source = 'Start\n---\n[Link](<https://example.com> "title")\nEnd'
    completed = StreamingSpeechSanitizer(markdown=markdown, urls=urls).process_complete(source)
    assert _stream(source, markdown=markdown, urls=urls) == _final_whitespace_cleanup(completed)
    if not markdown:
        assert "[Link](" in completed
        assert "---" in completed


@pytest.mark.parametrize(
    "source",
    [
        '[Label](https://example.com "unfinished)',
        "[Label](<https://example.com without close)",
        '[Label](<https://example.com> "title" trailing)',
        "[Label](https://example.com 'bad\nline')",
        "[Label](https://example.com",
        "[Label](https://example.com/a(b)",
        "Start\n--- not a rule\nEnd",
        "Start\n_ _ prose\nEnd",
        r"\*\*\* literal emphasis",
    ],
)
def test_malformed_link_and_rule_fallback_matches_complete_at_every_split(source):
    complete = _final_whitespace_cleanup(StreamingSpeechSanitizer().process_complete(source))
    for split in range(len(source) + 1):
        assert _stream(source, split) == complete, split


def test_long_completed_index_uses_link_title_and_angle_scanner():
    original = ("ordinary prose " * 320) + (
        ' Read [Source](<https://example.com/page(a)> "title") now.'
    )
    assert len(original) > 4096
    cleaned = process_speech_text(
        original,
        {
            "speech_processing_enabled": True,
            "speech_strip_markdown": True,
            "speech_strip_urls": True,
            "speech_regex_replacements": [],
        },
    )
    assert cleaned.endswith("Read Source now.")
    assert len(cleaned) < len(original)


def test_many_malformed_brackets_still_bounded_for_completed_parser():
    source = ("[invalid text " * 600) + "Final."
    start = perf_counter()
    result = StreamingSpeechSanitizer().process_complete(source)
    assert result.startswith("[invalid text")
    assert result.endswith("Final.")
    assert perf_counter() - start < 3.0



def test_completed_titled_link_is_validated_once_across_many_openers(monkeypatch):
    """Long link titles must not be rescanned for each malformed '[' prefix."""
    text = "[" * 1500 + 'caption](<https://example.com/path> "' + "title " * 1100 + '")'
    calls = 0
    original = StreamingSpeechSanitizer._scan_markdown_destination

    def counting_scanner(source, start, final, *, limit=None):
        nonlocal calls
        calls += 1
        return original(source, start, final, limit=limit)

    monkeypatch.setattr(
        StreamingSpeechSanitizer, "_scan_markdown_destination",
        staticmethod(counting_scanner),
    )
    result = StreamingSpeechSanitizer().process_complete(text)
    assert result.endswith("caption")
    assert calls == 1
