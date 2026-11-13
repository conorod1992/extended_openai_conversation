"""Bounded speech scope, empty citation items and arbitrary provider chunks."""

from hypothesis import given, settings, strategies as st
import pytest

from custom_components.extended_openai_conversation_responses.speech import (
    StreamingSpeechSanitizer as Sanitizer,
    _final_whitespace_cleanup as clean,
)


def streamed(source, width=1, **options):
    z = Sanitizer(**options)
    return clean(
        "".join(z.feed(source[i : i + width]) for i in range(0, len(source), width))
        + z.finish()
    )


def all_splits(source, expected, **options):
    assert clean(Sanitizer(**options).process_complete(source)) == expected
    assert streamed(source, **options) == expected
    for split in range(len(source) + 1):
        z = Sanitizer(**options)
        assert (
            clean(z.feed(source[:split]) + z.feed(source[split:]) + z.finish())
            == expected
        ), (source, split)


@pytest.mark.parametrize(
    "source,expected",
    [
        ("sensor_https://example.com_", "sensor_"),
        ("word*https://example.com*", "word*"),
        (r"\***-https://example.com**", r"\***-"),
        (
            "Sources:\n- https://example.com/a\n\n1. First step",
            "Sources:\n1. First step",
        ),
        ('Read more:\n- "https://example.com"\n\n2. Next', "Read more:\n2. Next"),
        ("* **https://example.com**", ""),
        ("* https://a.com (https://b.com) **Note** text", "Note text"),
        ("- Read https://example.com now\n2. Next", "Read now\nNext"),
        ("**read `code` https://example.com** after", "read code after"),
        ('**"read https://example.com"** after', '"read" after'),
        ('"**read https://example.com**" after', '"read" after'),
        ("2**3 https://example.com**", "2**3"),
        ("2**3** then https://example.com**", "23 then"),
        ("value ** 3 https://example.com**", "value ** 3"),
        ("ordinary_under_scores and 3.14", "ordinary_under_scores and 3.14"),
    ],
)
def test_reported_boundaries_and_negative_controls(source, expected):
    all_splits(source, expected)


@pytest.mark.parametrize("marker", ["-", "+", "*", "1.", "2)", "  -", "> -", "- >"])
@pytest.mark.parametrize(
    "link",
    [
        "https://example.com",
        '"https://example.com"',
        "**https://example.com**",
        "[example.com](https://example.com)",
        "https://a.com (https://b.com)",
    ],
)
def test_link_only_items_are_removed_without_losing_next_paragraph(marker, link):
    all_splits(f"Sources:\n{marker} {link}\n\n2. Next", "Sources:\n2. Next")


@pytest.mark.parametrize("kind", ["prose", "code", "quotes"])
def test_long_local_scope_is_independent_of_output_length(kind):
    prefix = {
        "prose": "Intro " * 800,
        "code": "`" + "x" * 5000 + " **literal` ",
        "quotes": "\"' " * 2000 + "\nIntro ",
    }[kind]
    source = prefix + "**read https://example.com** after"
    expected = clean(Sanitizer().process_complete(source))
    assert expected.endswith("read after") and not expected.endswith("**read after")
    for width in (1, 2, 7, 127, 4096, len(source)):
        assert streamed(source, width) == expected, width


@pytest.mark.parametrize("markdown", [True, False])
@pytest.mark.parametrize("urls", [True, False])
@settings(max_examples=80, deadline=None, derandomize=True)
@given(
    prefix=st.text(alphabet="abc XYZ_012 ", min_size=0, max_size=70),
    wrapper=st.sampled_from(["**", "*", "_", "~~", '"']),
    width=st.integers(min_value=1, max_value=17),
)
def test_supported_scopes_are_chunk_invariant(prefix, wrapper, width, markdown, urls):
    source = prefix + " " + wrapper + "read https://example.com" + wrapper + " after."
    expected = clean(Sanitizer(markdown=markdown, urls=urls).process_complete(source))
    assert streamed(source, width, markdown=markdown, urls=urls) == expected
    if not markdown and not urls:
        assert expected == clean(source)


def test_scope_memory_is_bounded_for_adversarial_unmatched_input():
    z = Sanitizer(max_buffer_chars=64)
    for _ in range(2000):
        z.feed("\"' **_ ")
        assert len(z._inline_scope.opened) <= 32
        assert len(z._pending_list_prefix) <= 32
        assert z.buffered_chars <= 128
    z.finish()


@pytest.mark.parametrize(
    "source",
    [
        '"\n2.  (https://example.com',
        "\\\n*https://example.com* next",
        "- \nhttps://example.com",
        "*\n2. https://example.com",
    ],
)
def test_incomplete_prefixes_keep_source_order_at_every_split(source):
    expected = clean(Sanitizer().process_complete(source))
    all_splits(source, expected)


def test_removed_link_whitespace_can_be_pending_in_either_stage():
    source = "*. \n2. https://example.com*"
    all_splits(source, clean(Sanitizer().process_complete(source)))
