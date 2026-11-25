"""Resolve actual empty wrappers at the URL/formatting stage boundary."""

from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses.const import (
    CONF_SPEECH_PROCESSING_ENABLED,
    CONF_SPEECH_STRIP_MARKDOWN,
    CONF_SPEECH_STRIP_URLS,
)
from custom_components.extended_openai_conversation_responses.speech import (
    StreamingSpeechSanitizer,
    _final_whitespace_cleanup as clean,
    async_streaming_speech_cleanup,
)
from tests.test_speech_scope_regressions import all_splits, streamed


@pytest.mark.parametrize(
    "opener,closer",
    [
        ("*", "*"),
        ("_", "_"),
        ("**", "**"),
        ("__", "__"),
        ("***", "***"),
        ("___", "___"),
        ("**_", "_**"),
        ("*__", "__*"),
        ('**"', '"**'),
        ('"**', '**"'),
    ],
)
@pytest.mark.parametrize(
    "links",
    [
        "https://a.com",
        "https://a.com https://b.com",
        "[a.com](https://a.com) https://b.com",
        "https://a.com [b.com](https://b.com)",
        "[a.com](https://a.com) [b.com](https://b.com)",
    ],
)
@pytest.mark.parametrize("gap", [" ", "\t", "\n", " \t "])
def test_empty_url_wrappers_across_all_splits(opener, closer, links, gap):
    expected = 'Before "" after.' if '"' in opener and " " in links else "Before after."
    all_splits(f"Before {opener}{links.replace(' ', gap)}{closer} after.", expected)


@pytest.mark.parametrize(
    "source,expected",
    [
        ("Before **https://a.com https://b.com** after.", "Before after."),
        ("Before (**https://a.com https://b.com**), after.", "Before (), after."),
        ("***Before **https://a.com https://b.com** after.***", "Before after."),
        ("**Before https://a.com https://b.com after.**", "Before after."),
        (
            "Before **https://a.com [Guide](https://b.com)** after.",
            "Before Guide after.",
        ),
        ("Before **https://a.com https://b.com text** after.", "Before text after."),
        ("Before **text https://a.com https://b.com** after.", "Before text after."),
        ("2**3 https://a.com https://b.com next", "2**3 next"),
        ("2 ** 3 https://a.com https://b.com next", "2 ** 3 next"),
        (r"\**https://a.com https://b.com** next", "** next"),
        (
            "sensor__temperature https://a.com https://b.com next",
            "sensor__temperature next",
        ),
        ("`**https://a.com https://b.com**`", "**https://a.com https://b.com**"),
        (
            "```\n**https://a.com https://b.com**\n```",
            "**https://a.com https://b.com**",
        ),
        ("1) **https://a.com https://b.com**\n2) [Guide](https://c.com)", "2. Guide"),
        ("***off*** **https://a.com https://b.com** ***now***", "off now"),
        (
            "Before **https://a.com https://b.com** **https://c.com https://d.com** after.",
            "Before after.",
        ),
        (
            r"Keep \_literal\_ https://a.com https://b.com next",
            "Keep _literal_ next",
        ),
        ("1) **https://a.com [Guide](https://b.com)**", "1. Guide"),
        ("1) **https://a.com https://b.com text**", "1. text"),
    ],
)
def test_wrapper_prose_and_literal_controls(source, expected):
    all_splits(source, expected)


@pytest.mark.parametrize("markdown", [True, False])
@pytest.mark.parametrize("urls", [True, False])
def test_independent_settings(markdown, urls):
    source = "Before **https://a.com https://b.com** after."
    expected = (
        "Before after."
        if urls
        else ("Before https://a.com https://b.com after." if markdown else source)
    )
    if urls and not markdown:
        expected = "Before ** after."
    all_splits(source, expected, markdown=markdown, urls=urls)


def test_long_completed_and_streamed_wrappers_keep_bounded_state():
    prefix = "Plain words. " * 400
    suffix = "Before **https://a.com https://b.com** after."
    source = prefix + suffix
    expected = prefix + "Before after."
    assert clean(StreamingSpeechSanitizer().process_complete(source)) == expected
    for width in (1, 7, 127, 4096):
        assert streamed(source, width) == expected
    z = StreamingSpeechSanitizer(max_buffer_chars=64)
    for _ in range(2000):
        z.feed(suffix + " ")
        assert z.buffered_chars <= 128
        assert len(z._inline_scope.opened) <= 32
    z.finish()


@pytest.mark.parametrize(
    "source,expected",
    [
        ("Before **https://a.com https://b.com** after.", "Before after."),
        ('A. **https://a.com https://b.com** "https://a.com" B.', "A. B."),
        (
            "1) First\n2) **https://a.com https://b.com**\n3) Third",
            "1. First\n3. Third",
        ),
    ],
)
def test_listener_text_needs_no_extra_whitespace_cleanup(source, expected):
    assert StreamingSpeechSanitizer().process_complete(source) == expected
    config = {
        CONF_SPEECH_PROCESSING_ENABLED: True,
        CONF_SPEECH_STRIP_MARKDOWN: True,
        CONF_SPEECH_STRIP_URLS: True,
    }
    for chunks in (
        [*source],
        *[[source[:i], source[i:]] for i in range(len(source) + 1)],
    ):
        received = []
        chat_log = SimpleNamespace(
            delta_listener=lambda _, delta, output=received: output.append(
                delta.get("content", "")
            )
        )
        with async_streaming_speech_cleanup(chat_log, config):
            for part in chunks:
                chat_log.delta_listener(chat_log, {"content": part})
        assert "".join(received) == expected
