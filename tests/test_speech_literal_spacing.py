"""Escaped punctuation retains authored separation through completed cleanup."""

import pytest

from custom_components.extended_openai_conversation_responses import regex_execution
from custom_components.extended_openai_conversation_responses.speech import (
    StreamingSpeechSanitizer,
    process_speech_text,
)
from tests.test_speech import _config
from tests.test_speech_replacement_safety import _ExecutorHass


@pytest.mark.parametrize(
    "source,expected",
    [
        (r"off now. \!photo", "off now. !photo"),
        (r"é ⚡ **off** now. \!photo", "é ⚡ off now. !photo"),
        (r"off now? \!photo", "off now? !photo"),
        (r"off now! \?photo", "off now! ?photo"),
        (r"Before \, after", "Before , after"),
        (r"Before \; after", "Before ; after"),
        (r"Before \: after", "Before : after"),
        (r"Before \. After", "Before . After"),
        (r"off now. \![photo](https://example.com)", "off now. !photo"),
        (r"See https://example.com/path. \!photo", "See. !photo"),
        (r"`off now. \!photo` and \!tail", r"off now. \!photo and !tail"),
        (r"Before \\!photo", r"Before \!photo"),
        (r"Before \\\!photo", r"Before \!photo"),
    ],
)
async def test_literal_spacing_matches_streams_at_every_provider_split(
    source, expected
):
    assert process_speech_text(source, _config()) == expected
    assert (
        await regex_execution.async_process_speech_text(
            _ExecutorHass(), source, _config()
        )
        == expected
    )
    for split in range(len(source) + 1):
        sanitizer = StreamingSpeechSanitizer()
        assert (
            sanitizer.feed(source[:split])
            + sanitizer.feed(source[split:])
            + sanitizer.finish()
            == expected
        )
    sanitizer = StreamingSpeechSanitizer()
    assert (
        "".join(sanitizer.feed(char) for char in source) + sanitizer.finish()
        == expected
    )


@pytest.mark.parametrize(
    "source,expected",
    [
        ("Hello , world !", "Hello, world!"),
        ("First . Next ? Yes ; done : fine !", "First. Next? Yes; done: fine!"),
        ("Read https://example.com , then stop !", "Read, then stop!"),
        ("off now. !photo", "off now.!photo"),
    ],
)
def test_ordinary_punctuation_attachment_is_unchanged(source, expected):
    assert process_speech_text(source, _config()) == expected


@pytest.mark.parametrize("markdown", [False, True])
@pytest.mark.parametrize("urls", [False, True])
def test_literal_spacing_respects_independent_cleanup_settings(markdown, urls):
    source = r"off now. \!photo https://example.com"
    expected = "off now. !photo" if markdown else r"off now. \!photo"
    if not urls:
        expected += " https://example.com"
    config = _config(speech_strip_markdown=markdown, speech_strip_urls=urls)
    assert process_speech_text(source, config) == expected
    assert (
        process_speech_text(source, _config(speech_processing_enabled=False)) == source
    )


@pytest.mark.parametrize(
    "rules,expected",
    [
        (
            [
                {"pattern": "photo", "replacement": "image"},
                {"pattern": "TOKEN", "replacement": " !"},
            ],
            "off now. !image!",
        ),
        (
            [
                {"pattern": r"(\. )(!)(photo)", "replacement": r"\1\2image"},
                {"pattern": " TOKEN", "replacement": ""},
            ],
            "off now. !image",
        ),
        (
            [
                {"pattern": r"(!)(photo)", "replacement": r"\2 \1 \1"},
                {"pattern": " TOKEN", "replacement": ""},
            ],
            "off now. photo ! !",
        ),
        (
            [
                {"pattern": r"^", "replacement": "prefix "},
                {"pattern": " TOKEN", "replacement": ""},
            ],
            "prefix off now. !photo",
        ),
        ([{"pattern": "!photo TOKEN", "replacement": " !image"}], "off now.!image"),
        ([{"pattern": ".", "replacement": "x" * 500}], "off now. !photo TOKEN"),
    ],
)
async def test_replacements_keep_order_and_only_copied_literal_provenance(
    rules, expected
):
    source = r"off now. \!photo TOKEN"
    config = _config(speech_regex_replacements=rules)
    assert process_speech_text(source, config) == expected
    assert (
        await regex_execution.async_process_speech_text(_ExecutorHass(), source, config)
        == expected
    )


async def test_failed_worker_preserves_literal_spacing(monkeypatch):
    async def fail(_payload):
        raise RuntimeError("unavailable")

    monkeypatch.setattr(regex_execution, "_async_run_regex_worker", fail)
    config = _config(
        speech_regex_replacements=[{"pattern": "photo", "replacement": "image"}]
    )
    assert (
        await regex_execution.async_process_speech_text(
            _ExecutorHass(), r"off now. \!photo", config
        )
        == "off now. !photo"
    )


@pytest.mark.parametrize("positions", [None, [-1], [999], [True], [0], [9, 9], [10, 9]])
async def test_invalid_worker_provenance_preserves_original_speech(
    monkeypatch, positions
):
    result = {
        "text": "off now. !image",
        "invalid": [],
        "literal_punctuation": positions,
    }

    async def worker(_payload):
        return result

    monkeypatch.setattr(regex_execution, "_async_run_regex_worker", worker)
    monkeypatch.setattr(regex_execution, "_run_regex_worker", lambda _payload: result)
    config = _config(
        speech_regex_replacements=[{"pattern": "photo", "replacement": "image"}]
    )
    source = r"off now. \!photo"
    assert process_speech_text(source, config) == "off now. !photo"
    assert (
        await regex_execution.async_process_speech_text(_ExecutorHass(), source, config)
        == "off now. !photo"
    )


def test_long_answers_preserve_literal_spacing_and_bounded_progressive_state():
    source = r"word \!photo " * 3000
    expected = ("word !photo " * 3000).strip()
    config = _config(
        speech_regex_replacements=[{"pattern": "photo", "replacement": "image"}]
    )
    # Oversized custom-regex input still uses the existing fail-open policy.
    assert process_speech_text(source, config) == expected
    sanitizer = StreamingSpeechSanitizer()
    result = (
        "".join(
            sanitizer.feed(source[index : index + 7])
            for index in range(0, len(source), 7)
        )
        + sanitizer.finish()
    )
    assert result.strip() == expected
    assert sanitizer.max_buffered_chars < 32


@pytest.mark.parametrize(
    "source,expected",
    [
        (r"*hello \!world*", "hello !world"),
        (r"_hello \!world_", "hello !world"),
        (r"*prefix **hello \!world** tail \?end*", "prefix hello !world tail ?end"),
        (r"**outer _hello \!world_ tail**", "outer hello !world tail"),
        (r"*one \!two _three \?four_*", "one !two three ?four"),
        (r"*hello `\!world` \!literal*", r"hello \!world !literal"),
        (r"*hello \\\!world*", r"hello \!world"),
    ],
)
async def test_nested_formatting_copies_literal_provenance(source, expected):
    assert process_speech_text(source, _config()) == expected
    for split in range(len(source) + 1):
        sanitizer = StreamingSpeechSanitizer()
        assert (
            sanitizer.feed(source[:split])
            + sanitizer.feed(source[split:])
            + sanitizer.finish()
            == expected
        )
        assert sanitizer.max_buffered_chars <= len(source)
    sanitizer = StreamingSpeechSanitizer()
    assert (
        "".join(sanitizer.feed(char) for char in source) + sanitizer.finish()
        == expected
    )
    config = _config(
        speech_regex_replacements=[
            {"pattern": r"(!)(world)", "replacement": r"\2 \1"},
            {"pattern": "world", "replacement": "image"},
        ]
    )
    # A copied punctuation group retains its origin after nested formatting.
    replaced = (
        r"hello \image! !literal"
        if "`" in source
        else expected.replace("!world", "image !")
    )
    assert process_speech_text(source, config) == replaced
    assert (
        await regex_execution.async_process_speech_text(_ExecutorHass(), source, config)
        == replaced
    )


def test_nested_provenance_offsets_on_indexed_long_input():
    prefix = "é word " * 1000
    source = prefix + r"*hello \!world* then \?tail"
    assert process_speech_text(source, _config()) == prefix + "hello !world then ?tail"
    assert process_speech_text("*hello !world*", _config()) == "hello!world"
