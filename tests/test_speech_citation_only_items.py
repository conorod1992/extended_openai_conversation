"""Citation punctuation and later empty wrappers must not release list numbers."""

import pytest

from custom_components.extended_openai_conversation_responses.speech import (
    StreamingSpeechSanitizer as Sanitizer,
)
from tests.test_speech_scope_regressions import all_splits


@pytest.mark.parametrize("prefix", ["2) ", "2. ", "> 2) ", "* 2) "])
@pytest.mark.parametrize(
    "tail",
    [
        "https://example.com.",
        "https://a.com **[b.com](https://b.com)**",
        "https://a.com. **[b.com](https://b.com)**.",
        "https://a.com __[b.com](https://b.com)__!",
        "https://a.com ***[b.com](https://b.com)***.",
        "[a.com](https://a.com), [b.com](https://b.com).",
    ],
)
@pytest.mark.parametrize("ending", ["", "\n", "\r\n"])
def test_citation_only_items_without_whitespace_cleanup(prefix, tail, ending):
    source = prefix + tail + ending
    assert Sanitizer().process_complete(source) == ""
    for chunks in (
        [*source],
        *[[source[:i], source[i:]] for i in range(len(source) + 1)],
    ):
        z = Sanitizer()
        assert "".join(z.feed(part) for part in chunks) + z.finish() == "", (
            source,
            chunks,
        )


@pytest.mark.parametrize(
    "source,expected",
    [
        ("2) https://a.com **[Guide](https://b.com)**", "2. Guide"),
        ("2) https://example.com. Check", "2.. Check"),
        ("2) https://example.com .5 volts", "2. .5 volts"),
        ("2) https://example.com -3 degrees", "2. -3 degrees"),
        ("2) https://example.com `2**3`", "2. 2**3"),
        (r"2) https://example.com \*literal\*", r"2. \*literal\*"),
        ("2) https://a.com **[b.com](https://b.com)** Next", "2. Next"),
        ("1) First\n2) https://example.com.\n3) Third", "1. First\n3. Third"),
        (
            "1) First\n2) https://a.com **[b.com](https://b.com)**\n3) Third",
            "1. First\n3. Third",
        ),
    ],
)
def test_readable_content_and_neighboring_items(source, expected):
    all_splits(source, expected)


def test_suppressed_item_prefix_remains_bounded():
    z = Sanitizer(max_buffer_chars=64)
    z.feed("2) https://a.com ")
    for _ in range(1000):
        z.feed(". **[b.com](https://b.com)** ")
        assert len(z._pending_list_prefix) <= 32
        assert z.buffered_chars <= 128
    z.finish()
