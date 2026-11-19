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


@pytest.mark.parametrize("dash", ["—", "–"])
@pytest.mark.parametrize("wrapper", ["{}", '"{}"', "**{}**"])
def test_unspaced_dash_ends_bare_url_at_every_split(dash, wrapper):
    url = wrapper.format("http://router.local:8080")
    source = f"Router instructions: {url}{dash}never press Reset."
    assert_all_splits(source, f"Router instructions: {dash}never press Reset.")


@pytest.mark.parametrize(
    "url",
    [
        "https://demo\u2013alias@example.invalid/guide",
        "https://demo:sample\u2014label@example.invalid",
        "https://demo\u2014alias:sample\u2013label@example.invalid",
    ],
)
@pytest.mark.parametrize("wrapper", ["{}", '"{}"', "**{}**"])
def test_bare_url_keeps_userinfo_dashes_hidden_at_every_split(url, wrapper):
    assert_all_splits(f"Read {wrapper.format(url)} first.", "Read first.")


def test_bare_url_resumes_prose_after_the_host_following_userinfo():
    assert_all_splits(
        "Router: https://demo\u2013alias@router.local:8080\u2014never press Reset.",
        "Router: \u2014never press Reset.",
    )


@pytest.mark.parametrize(
    "source,expected",
    [
        ('Open "{}"', "Open "),
        ("Open \u201c{}\u201d", "Open "),
        ("Open **{}**", "Open "),
        ("**Open {}**", "Open"),
        ("\u201cOpen {}\u201d", "\u201cOpen\u201d"),
    ],
)
@pytest.mark.parametrize(
    "url", ["https://example.invalid", "https://example.invalid/guide?x=1"]
)
def test_wrapper_closer_ends_url_before_prose_with_an_email(source, expected, url):
    suffix = "\u2014contact@example.invalid can help."
    assert_all_splits(source.format(url) + suffix, expected + suffix)


@pytest.mark.parametrize(
    "source,expected",
    [
        (
            'Open "https://demo\u2013alias@example.invalid"\u2014contact@example.invalid can help.',
            "Open \u2014contact@example.invalid can help.",
        ),
        (
            "Open **https://demo:sample**\u2013label@example.invalid**\u2014then continue.",
            "Open \u2014then continue.",
        ),
        (
            "Open **https://demo:sample\\**\u2013label@example.invalid** first.",
            "Open first.",
        ),
        (
            "Open https://demo**\u2013label@example.invalid first.",
            "Open first.",
        ),
    ],
)
def test_wrapper_before_dash_respects_enclosing_and_escaped_markers(source, expected):
    assert_all_splits(source, expected)


@pytest.mark.parametrize(
    "source,expected",
    [('Open "{}"', "Open "), ("Open **{}**", "Open "), ("**Open {}**", "Open")],
)
def test_overlong_wrapped_url_ends_before_prose_with_an_email(source, expected):
    url = "https://example.invalid/" + "a" * 280
    suffix = "\u2014contact@example.invalid can help."
    raw = source.format(url) + suffix
    expected += suffix
    assert StreamingSpeechSanitizer(max_buffer_chars=64).process_complete(raw) == expected
    for width in (1, 13, 64, len(raw)):
        sanitizer = StreamingSpeechSanitizer(max_buffer_chars=64)
        chunks = []
        for start in range(0, len(raw), width):
            chunks.append(sanitizer.feed(raw[start : start + width]))
            assert sanitizer.buffered_chars <= sanitizer.max_buffer_chars + 256
        assert "".join(chunks) + sanitizer.finish() == expected, width


@pytest.mark.parametrize("wrapper", ["{}", '"{}"', "**{}**"])
@pytest.mark.parametrize("left,right", [(96, 5), (4, 96), (96, 96), (4, 64), (4, 63)])
def test_long_bare_url_keeps_userinfo_dashes_hidden_with_bounded_buffers(
    wrapper, left, right
):
    url = wrapper.format(f"https://{'a' * left}\u2013{'b' * right}@example.invalid")
    source = f"Read {url}\u2014then continue."
    expected = "Read \u2014then continue."
    assert StreamingSpeechSanitizer(max_buffer_chars=64).process_complete(source) == expected
    for width in (1, 13, 64, len(source)):
        sanitizer = StreamingSpeechSanitizer(max_buffer_chars=64)
        chunks = []
        for start in range(0, len(source), width):
            chunks.append(sanitizer.feed(source[start : start + width]))
            assert sanitizer.buffered_chars <= sanitizer.max_buffer_chars + 256
        assert "".join(chunks) + sanitizer.finish() == expected, width


@pytest.mark.parametrize("wrapper", ["{}", '"{}"', "**{}**"])
def test_unspaced_dash_ends_overlong_bare_url(wrapper):
    url = wrapper.format("http://" + "a" * 4200 + ".local:8080")
    source = f"Router instructions: {url}—never press Reset."
    expected = "Router instructions: —never press Reset."
    for width in (1, 71, 4096, len(source)):
        sanitizer = StreamingSpeechSanitizer()
        assert (
            "".join(
                sanitizer.feed(source[index : index + width])
                for index in range(0, len(source), width)
            )
            + sanitizer.finish()
            == expected
        ), width


@pytest.mark.parametrize(
    "url",
    [
        "https://en.wikipedia.org/wiki/Boston–Washington_Corridor",
        "https://example.invalid/device—red/details",
        "https://example.invalid?search=Boston–Washington",
        "https://example.invalid#Boston–Washington",
    ],
)
def test_bare_url_keeps_path_query_and_fragment_dashes_hidden(url):
    assert_all_splits(f"See {url} for details.", "See for details.")


def test_overlong_bare_url_keeps_query_dash_hidden_across_buffers():
    url = "https://example.invalid?query=" + "a" * 4200 + "–dash"
    source = f"See {url} for details."
    for width in (1, 71, 4096, len(source)):
        sanitizer = StreamingSpeechSanitizer()
        assert (
            "".join(
                sanitizer.feed(source[index : index + width])
                for index in range(0, len(source), width)
            )
            + sanitizer.finish()
            == "See for details."
        ), width


@pytest.mark.parametrize(
    "wrapper",
    [
        "[Download]({})",
        "([Download]({}))",
        '[Download](<{}> "Title")',
        "![Image]({})",
        "[example.com]({})",
        "<{}>",
        "({})",
        '"{}"',
        '"**{}**"',
        "**{}**",
        "[{}]",
    ],
)
@pytest.mark.parametrize("chunk_size", [13, 128, 4096])
def test_long_url_wrappers_match_completed_speech(wrapper, chunk_size):
    url = "https://example.com/(nested)/?signature=" + "x" * 4200
    source = "Before " + wrapper.format(url) + ", after."
    expected = StreamingSpeechSanitizer().process_complete(source)
    sanitizer = StreamingSpeechSanitizer()
    chunks = []
    for start in range(0, len(source), chunk_size):
        chunks.append(sanitizer.feed(source[start : start + chunk_size]))
        assert sanitizer.buffered_chars <= sanitizer.max_buffer_chars + 256
    chunks.append(sanitizer.finish())
    assert "".join(chunks) == expected
    assert "signature" not in "".join(chunks)


@pytest.mark.parametrize(
    "template,expected",
    [
        ('Before [Download]({} "Title"), after.', "Before Download, after."),
        ('Before ([Download](<{}> "Title")), after.', "Before Download, after."),
        ("Before[Download]({}), after.", "BeforeDownload, after."),
    ],
)
def test_overlong_readable_link_preserves_spacing_at_the_buffer_boundary(
    template, expected
):
    source = template.format("https://example.com/" + "x" * 4200)
    assert StreamingSpeechSanitizer().process_complete(source) == expected
    for width in (1, 11, 97, 4096, len(source)):
        sanitizer = StreamingSpeechSanitizer()
        assert (
            "".join(
                sanitizer.feed(source[index : index + width])
                for index in range(0, len(source), width)
            )
            + sanitizer.finish()
            == expected
        ), width


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
            "First\n2. Second\n3. Third",
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
        (r"Before \*https://example.com* after", "Before * after"),
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
        (
            "- First\n3. Third https://example.com\n7. Seventh",
            "First\n3. Third\n7. Seventh",
        ),
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
