"""URL wrappers and list boundaries are independent of provider chunks."""

import pytest

from custom_components.extended_openai_conversation_responses.speech import (
    StreamingSpeechSanitizer,
    _final_whitespace_cleanup,
)


@pytest.mark.parametrize(
    "opening,closing",
    [
        ("**", "**"),
        ("*", "*"),
        ("_", "_"),
        ("__", "__"),
        ("~~", "~~"),
        ("***", "***"),
        ("**_", "_**"),
        ("_**", "**_"),
        ('"', '"'),
        ("'", "'"),
        ("“", "”"),
        ("\u2018", "\u2019"),
        ('"**', '**"'),
        ('**"', '"**'),
    ],
)
def test_bare_url_wrappers_at_every_split(opening, closing):
    source = f"Before {opening}https://example.com/path{closing} after."
    assert_all_splits(source, "Before after.")


def assert_all_splits(source, expected):
    assert (
        _final_whitespace_cleanup(StreamingSpeechSanitizer().process_complete(source))
        == expected
    )
    for chunks in (
        [source],
        list(source),
        *[[source[:i], source[i:]] for i in range(len(source) + 1)],
    ):
        sanitizer = StreamingSpeechSanitizer()
        actual = "".join(
            [*(sanitizer.feed(chunk) for chunk in chunks), sanitizer.finish()]
        )
        assert _final_whitespace_cleanup(actual) == expected, chunks


@pytest.mark.parametrize(
    "source,expected",
    [
        (
            "1. First https://example.com\n2. Second\n3. Third",
            "1. First\n2. Second\n3. Third",
        ),
        (
            "- First https://example.com\n2. Second https://example.com\n3. Third",
            "First\nSecond\nThird",
        ),
        ("- First https://example.com\n- Second", "First\nSecond"),
        ("First https://example.com\n## Heading", "First\nHeading"),
        ("First https://example.com\n7. Juli", "First 7. Juli"),
        ("1. First https://example.com\n3.14 is pi", "1. First 3.14 is pi"),
        ("1. May https://example.com\n42. Total", "1. May 42. Total"),
        (
            "First https://example.com\nordinary continuation",
            "First ordinary continuation",
        ),
        ("Before https://example.com/path** after", "Before after"),
        ("Before https://example.com/path_ after", "Before after"),
        ('Before https://example.com/path" after', "Before after"),
        (r"Before \*https://example.com* after", r"Before \* after"),
        (
            "Before `**https://example.com**` after",
            "Before **https://example.com** after",
        ),
        (
            "Before **readable** https://example.com/path** after",
            "Before readable after",
        ),
        ('Before "https://example.com/path\'s" after', "Before after"),
        ('Before "https://example.com/path". After', "Before. After"),
        ("Before **https://example.com/path**. After", "Before. After"),
    ],
)
def test_url_boundaries_and_negative_controls(source, expected):
    assert_all_splits(source, expected)


@pytest.mark.parametrize(
    "source,expected",
    [
        ("Before **read https://example.com** after", "Before read after"),
        ("Before *read https://example.com* after", "Before read after"),
        ("Before _read https://example.com_ after", "Before read after"),
        ("Before ~~read https://example.com~~ after", "Before read after"),
        ("Before **_read https://example.com_** after", "Before read after"),
        ('Before "read https://example.com" after', 'Before "read" after'),
        ('Before "**read https://example.com**" after', 'Before "read" after'),
    ],
)
def test_url_at_end_of_readable_wrapper(source, expected):
    assert_all_splits(source, expected)


def test_unbalanced_quote_tracking_is_bounded():
    sanitizer = StreamingSpeechSanitizer()
    sanitizer.feed(("\"' " * 1000) + "text ")
    assert len(sanitizer._inline_scope.opened) <= 32


@pytest.mark.parametrize(
    "source,expected",
    [
        ("1. First https://example.com\n1. Second", "1. First\n1. Second"),
        ("- First\n3. Third https://example.com\n7. Seventh", "First\nThird\nSeventh"),
        ("- 3 degrees https://example.com\n7. Juli", "- 3 degrees 7. Juli"),
    ],
)
def test_repeated_and_established_numbered_sequences(source, expected):
    assert_all_splits(source, expected)


@pytest.mark.parametrize("marker", ["**", "*", "_", "~~"])
def test_long_code_cannot_authorize_a_url_path_suffix(marker):
    literal = "x" * 5000 + " " + marker + "literal"
    source = "`" + literal + "` after https://example.com/path" + marker + " done"
    expected = literal + " after done"
    assert (
        _final_whitespace_cleanup(StreamingSpeechSanitizer().process_complete(source))
        == expected
    )
    for width in (1, 127, 4096, len(source)):
        sanitizer = StreamingSpeechSanitizer()
        actual = (
            "".join(
                sanitizer.feed(source[i : i + width])
                for i in range(0, len(source), width)
            )
            + sanitizer.finish()
        )
        assert _final_whitespace_cleanup(actual) == expected, width
