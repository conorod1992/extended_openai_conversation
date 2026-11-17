"""Image introducers respect the existing escaped-literal contract."""

import pytest

from custom_components.extended_openai_conversation_responses.speech import (
    StreamingSpeechSanitizer,
    process_speech_text,
)
from tests.test_speech import _config
from tests.test_speech_scope_regressions import all_splits


@pytest.mark.parametrize("slashes", range(7))
@pytest.mark.parametrize("parenthesized", [False, True])
def test_image_backslash_parity_at_every_boundary(slashes, parenthesized):
    prefix = "\\" * slashes
    source = prefix + "![a photograph](https://example.com/image.png)"
    spoken_prefix = "\\" * (slashes // 2)
    expected = spoken_prefix + "!a photograph" if slashes % 2 else spoken_prefix
    if parenthesized:
        source = "(" + source + ")"
        # The literal parentheses around an escaped introducer remain speech.
        expected = "(" + expected + ")" if slashes else ""
    all_splits(source, expected)


@pytest.mark.parametrize("source,expected", [
    ("![a photograph](https://example.com/image.png)", ""),
    ("[a photograph](https://example.com/image.png)", "a photograph"),
    (r"\![a photograph](https://example.com/image.png)", "!a photograph"),
    (r"\!\[a photograph]", "![a photograph]"),
    (r"\![unfinished", "![unfinished"),
    (r"`\![photo](https://example.com/image.png)`", r"\![photo](https://example.com/image.png)"),
    (r"![photo\]](https://example.com/image.png)", ""),
])
def test_images_links_malformed_escapes_and_code(source, expected):
    all_splits(source, expected)


@pytest.mark.parametrize("markdown", [False, True])
@pytest.mark.parametrize("urls", [False, True])
def test_independent_cleanup_settings(markdown, urls):
    source = r"\![a photograph](https://example.com/image.png)"
    expected = "!a photograph" if markdown else (
        r"\![a photograph]" if urls else source
    )
    all_splits(source, expected, markdown=markdown, urls=urls)


def test_long_completed_index_and_custom_replacement_order():
    prefix = "Plain words. " * 400
    source = prefix + r"\![a photograph](https://example.com/image.png)"
    sanitizer = StreamingSpeechSanitizer()
    assert sanitizer.process_complete(source) == prefix + "!a photograph"
    assert sanitizer._completed_links is not None
    assert process_speech_text(source, _config(speech_regex_replacements=[
        {"pattern": r"!a photograph", "replacement": "photo"},
    ])) == prefix + "photo"


def test_all_speech_fixes_together():
    source = (
        "Result\n5.\nDone\nThe switch is ***off*** "
        "[a.com](https://a.com) [b.com](https://b.com) ***now***. "
        r"\![a photograph](https://example.com/image.png)"
    )
    expected = "Result\n5.\nDone\nThe switch is off now. !a photograph"
    assert process_speech_text(source, _config()) == expected
    # Real progressive output already preserves this literal separation; do not
    # run another completed cleanup on concatenated, provenance-free deltas.
    for split in range(len(source) + 1):
        sanitizer = StreamingSpeechSanitizer()
        assert sanitizer.feed(source[:split]) + sanitizer.feed(source[split:]) + sanitizer.finish() == expected
